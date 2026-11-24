"""Tests for the custom-server mode of scripts/benchmark.py (argument rules, prefix stripping, VRAM report)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "benchmark.py"


@pytest.fixture(scope="module")
def bench():
    """Load the script as a module; third-party imports it only needs for real runs are stubbed while it loads."""
    patch = pytest.MonkeyPatch()
    for name, attrs in {
        "numpy": {},
        "requests": {"post": None, "exceptions": types.SimpleNamespace(Timeout=TimeoutError, ConnectionError=OSError)},
        "datasets": {"load_dataset": None},
        "jiwer": {"wer": None},
    }.items():
        if importlib.util.find_spec(name) is None:
            stub = types.ModuleType(name)
            for key, value in attrs.items():
                setattr(stub, key, value)
            patch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location("benchmark_script", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    patch.undo()  # the stubs only had to exist while the script imported them
    return module


def _parse(bench, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["benchmark.py", *argv])
    return bench.parse_args()


URL = "http://127.0.0.1:8008/v1/audio/transcriptions"
CUSTOM = ("--model", "custom-server", "--url", URL)


def test_custom_server_needs_a_url(bench, monkeypatch):
    with pytest.raises(SystemExit):
        _parse(bench, monkeypatch, "--model", "custom-server")


@pytest.mark.parametrize("url", ["127.0.0.1:8008/v1/audio/transcriptions", "ftp://host/x", "http://"])
def test_custom_server_rejects_a_url_that_is_not_http(bench, monkeypatch, url):
    with pytest.raises(SystemExit):
        _parse(bench, monkeypatch, "--model", "custom-server", "--url", url)


def test_custom_server_rejects_cpu_device(bench, monkeypatch):
    with pytest.raises(SystemExit):
        _parse(bench, monkeypatch, *CUSTOM, "--device", "cpu")


def test_model_name_is_rejected_for_a_bundled_model(bench, monkeypatch):
    with pytest.raises(SystemExit):
        _parse(bench, monkeypatch, "--model", "whisper-large-v3-turbo", "--model-name", "mine")


def test_custom_server_uses_the_model_name_and_both_languages(bench, monkeypatch):
    args = _parse(bench, monkeypatch, *CUSTOM, "--model-name", "qwen3-asr", "--lang", "both")
    assert args.url == URL
    assert args.model_hf_name == "qwen3-asr"
    assert args.langs == ["en_us", "de_de"]


def test_custom_server_name_defaults_to_the_slot_name(bench, monkeypatch):
    assert _parse(bench, monkeypatch, *CUSTOM).model_hf_name == "custom-server"


def test_list_models_does_not_call_the_custom_server_unsupported(bench, capsys):
    bench.list_models()
    out = capsys.readouterr().out
    assert "Your own server" in out
    assert "custom-server" not in out.partition("Not supported")[2]


def _http_error(code):
    return HTTPError("http://x/health", code, "msg", {}, None)


def test_health_check_stops_while_the_model_is_loading(bench, monkeypatch):
    def boom(*a, **k):
        raise _http_error(503)

    monkeypatch.setattr(bench, "urlopen", boom)
    with pytest.raises(SystemExit, match="still loading"):
        bench.custom_health_check(URL)


def test_health_check_accepts_a_server_without_a_health_route(bench, monkeypatch):
    def boom(*a, **k):
        raise _http_error(404)

    monkeypatch.setattr(bench, "urlopen", boom)
    bench.custom_health_check(URL)


def test_transcribe_strips_the_language_marker(bench, monkeypatch):
    response = MagicMock(ok=True)
    response.json.return_value = {"text": "language English<asr_text>Hello there."}
    monkeypatch.setattr(bench.requests, "post", lambda *a, **k: response)
    text, latency, error = bench.transcribe(b"wav", URL, "qwen3-asr", 5.0)
    assert (text, error) == ("Hello there.", None)
    assert latency is not None


def _readings(monkeypatch, bench, *values):
    it = iter(values)
    last = values[-1]
    monkeypatch.setattr(bench.VramMonitor, "read_mib", staticmethod(lambda: next(it, last)))


def test_vram_summary_reports_the_peak_and_its_increase(bench, monkeypatch):
    _readings(monkeypatch, bench, [5800], [14400], [14500], [14300])
    monitor = bench.VramMonitor(interval_s=3600)
    monitor.start()
    monitor.sample()
    monitor.sample()
    monitor.stop()
    assert monitor.summary() == {
        "vram_gpu_index": 0,
        "vram_start_mib": 5800,
        "vram_peak_mib": 14500,
        "vram_peak_increase_mib": 8700,
        "vram_run_start_mib": 5800,
    }


def test_vram_follows_the_gpu_that_grew_not_the_busiest_one(bench, monkeypatch):
    _readings(monkeypatch, bench, [20000, 6000], [20000, 6400])
    monitor = bench.VramMonitor(interval_s=3600)
    monitor.start()
    monitor.stop()
    summary = monitor.summary()
    assert (summary["vram_gpu_index"], summary["vram_peak_mib"], summary["vram_peak_increase_mib"]) == (1, 6400, 400)


def test_vram_baseline_puts_the_model_weights_into_the_increase(bench, monkeypatch):
    _readings(monkeypatch, bench, [14000], [14400])
    monitor = bench.VramMonitor(interval_s=3600, baseline_mib=10000)
    monitor.start()
    monitor.stop()
    summary = monitor.summary()
    assert (summary["vram_start_mib"], summary["vram_peak_increase_mib"], summary["vram_run_start_mib"]) == (
        10000,
        4400,
        14000,
    )


def test_vram_values_stay_none_without_a_gpu(bench, monkeypatch):
    monkeypatch.setattr(bench.VramMonitor, "read_mib", staticmethod(lambda: None))
    monitor = bench.VramMonitor()
    monitor.start()
    monitor.stop()
    assert set(monitor.summary().values()) == {None}


def test_read_mib_survives_a_missing_nvidia_smi(bench, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(bench.subprocess, "run", boom)
    assert bench.VramMonitor.read_mib() is None


def test_read_mib_returns_one_value_per_gpu(bench, monkeypatch):
    monkeypatch.setattr(bench.subprocess, "run", lambda *a, **k: MagicMock(stdout="20000\n6000\n"))
    assert bench.VramMonitor.read_mib() == [20000, 6000]
