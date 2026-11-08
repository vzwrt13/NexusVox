"""Tests for the custom-server mode of scripts/benchmark.py (argument rules, prefix stripping, VRAM report)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "benchmark.py"


@pytest.fixture(scope="module")
def bench():
    """Load the script as a module; heavy third-party imports it only needs for real runs are stubbed if absent."""
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
            sys.modules[name] = stub
    spec = importlib.util.spec_from_file_location("benchmark_script", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse(bench, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["benchmark.py", *argv])
    return bench.parse_args()


URL = "http://127.0.0.1:8008/v1/audio/transcriptions"


def test_custom_server_needs_a_url(bench, monkeypatch):
    with pytest.raises(SystemExit):
        _parse(bench, monkeypatch, "--model", "custom-server")


def test_custom_server_rejects_cpu_device(bench, monkeypatch):
    with pytest.raises(SystemExit):
        _parse(bench, monkeypatch, "--model", "custom-server", "--url", URL, "--device", "cpu")


def test_custom_server_uses_the_model_name_and_both_languages(bench, monkeypatch):
    args = _parse(
        bench, monkeypatch, "--model", "custom-server", "--url", URL, "--model-name", "qwen3-asr", "--lang", "both"
    )
    assert args.url == URL
    assert args.model_hf_name == "qwen3-asr"
    assert args.langs == ["en_us", "de_de"]


def test_custom_server_name_defaults_to_the_slot_name(bench, monkeypatch):
    assert _parse(bench, monkeypatch, "--model", "custom-server", "--url", URL).model_hf_name == "custom-server"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("language English<asr_text>Hello there.", "Hello there."),
        ("language German<asr_text>Guten Tag.", "Guten Tag."),
        ("Plain text", "Plain text"),
    ],
)
def test_custom_server_transcripts_lose_the_language_marker(bench, raw, expected):
    assert bench.clean_hypothesis(raw, "custom-server") == expected


def test_other_models_keep_the_text_untouched(bench):
    assert bench.clean_hypothesis("language English<asr_text>x", "parakeet-tdt-0.6b") == "language English<asr_text>x"


def test_transcribe_strips_the_marker_for_custom_server(bench, monkeypatch):
    response = MagicMock(ok=True)
    response.json.return_value = {"text": "language English<asr_text>Hello there."}
    monkeypatch.setattr(bench.requests, "post", lambda *a, **k: response)
    text, latency, error = bench.transcribe(b"wav", URL, "qwen3-asr", 5.0, "custom-server")
    assert (text, error) == ("Hello there.", None)
    assert latency is not None


def test_vram_summary_reports_the_peak_and_its_increase(bench, monkeypatch):
    readings = iter([5800, 14400, 14500, 14300])
    monkeypatch.setattr(bench.VramMonitor, "read_mib", staticmethod(lambda: next(readings, 14300)))
    monitor = bench.VramMonitor(interval_s=3600)
    monitor.start()
    monitor.sample()
    monitor.sample()
    monitor.stop()
    assert monitor.summary() == {"vram_start_mib": 5800, "vram_peak_mib": 14500, "vram_peak_increase_mib": 8700}


def test_vram_values_stay_none_without_a_gpu(bench, monkeypatch):
    monkeypatch.setattr(bench.VramMonitor, "read_mib", staticmethod(lambda: None))
    monitor = bench.VramMonitor()
    monitor.start()
    monitor.stop()
    assert monitor.summary() == {"vram_start_mib": None, "vram_peak_mib": None, "vram_peak_increase_mib": None}


def test_read_mib_survives_a_missing_nvidia_smi(bench, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(bench.subprocess, "run", boom)
    assert bench.VramMonitor.read_mib() is None
