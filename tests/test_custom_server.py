"""Tests for the "custom-server" model slot: a user-run server reached over HTTP or the
realtime WebSocket, configured under [inference.custom] and never started through Docker.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nexusvox.config import (
    Config,
    CustomServerConfig,
    InferenceConfig,
    load_config,
    model_needs_docker,
    save_config,
)
from nexusvox.transcriber import OpenAIHttpTranscriber, VoxtralRealtimeTranscriber, create_transcriber


def _custom(url: str = "http://bridge:8765/v1/audio/transcriptions", protocol: str = "http", **kw) -> InferenceConfig:
    return InferenceConfig(model="custom-server", custom=CustomServerConfig(url=url, protocol=protocol, **kw))


# ---- Factory ---------------------------------------------------------------


def test_http_protocol_builds_http_client_on_the_custom_url():
    transcriber = create_transcriber(_custom(), device="cuda")
    assert isinstance(transcriber, OpenAIHttpTranscriber)
    assert transcriber._server_url == "http://bridge:8765/v1/audio/transcriptions"
    assert transcriber.needs_docker is False


def test_realtime_protocol_builds_streaming_client():
    transcriber = create_transcriber(_custom("ws://bridge:8765/v1/realtime", "realtime"), device="cuda")
    assert isinstance(transcriber, VoxtralRealtimeTranscriber)
    assert transcriber._server_url == "ws://bridge:8765/v1/realtime"
    assert transcriber.needs_docker is False


def test_custom_server_works_on_cpu():
    # Nothing runs locally, so a GPU-less machine can use it too.
    transcriber = create_transcriber(_custom(), device="cpu")
    assert isinstance(transcriber, OpenAIHttpTranscriber)


def test_model_name_is_sent_and_recorded():
    transcriber = create_transcriber(_custom(model_name="assemblyai-universal"), device="cuda")
    assert transcriber.model == "assemblyai-universal"


def test_custom_url_does_not_touch_the_main_server_url():
    config = _custom()
    create_transcriber(config, device="cuda")
    assert config.server_url == InferenceConfig().server_url


def test_missing_url_raises_with_a_hint():
    with pytest.raises(RuntimeError, match=r"\[inference.custom\].url"):
        create_transcriber(_custom(url=""), device="cuda")


def test_unknown_protocol_raises():
    with pytest.raises(ValueError, match="protocol"):
        create_transcriber(_custom(protocol="grpc"), device="cuda")


# ---- Docker decision -------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "device", "expected"),
    [
        ("custom-server", "cuda", False),
        ("custom-server", "cpu", False),
        ("parakeet-tdt-0.6b", "cuda", True),
        ("whisper-large-v3-turbo", "cuda", True),
        ("whisper-large-v3-turbo", "cpu", False),
    ],
)
def test_model_needs_docker(model, device, expected):
    assert model_needs_docker(model, device) is expected


# ---- Config ----------------------------------------------------------------


def test_custom_section_roundtrips(tmp_path):
    original = Config(inference=_custom("ws://bridge:8765/v1/realtime", "realtime", model_name="assemblyai"))
    path = tmp_path / "config.toml"
    save_config(original, path)
    loaded = load_config(path)
    assert loaded.inference.model == "custom-server"
    assert loaded.inference.custom == original.inference.custom


def test_custom_section_defaults_when_absent(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[inference]\nmodel = "parakeet-tdt-0.6b"\n', encoding="utf-8")
    assert load_config(path).inference.custom == CustomServerConfig()


def test_unknown_protocol_in_toml_falls_back_to_http(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[inference.custom]\nurl = "http://x"\nprotocol = "grpc"\n', encoding="utf-8")
    assert load_config(path).inference.custom.protocol == "http"


# ---- Dashboard -------------------------------------------------------------


def test_dashboard_lists_custom_server_with_configured_protocol(flask_client):
    data = flask_client.get("/api/models").get_json()
    custom = next(m for m in data if m["id"] == "custom-server")
    assert custom["custom"] is True
    assert custom["protocol"] == "openai_http"
    assert custom["streaming"] is False
    assert custom["server_url"] == ""
    parakeet = next(m for m in data if m["id"] == "parakeet-tdt-0.6b")
    assert parakeet["custom"] is False


# ---- App model switch ------------------------------------------------------


def _switch(model_id: str, inference: InferenceConfig, monkeypatch):
    try:
        from nexusvox.app import NexusVoxApp
    except Exception:  # Windows-only imports on other platforms
        pytest.skip("nexusvox.app needs the Windows runtime")

    docker = MagicMock()
    monkeypatch.setattr("nexusvox.app.docker_ctl", docker)
    monkeypatch.setattr("nexusvox.app.save_config", lambda cfg: None)

    app = NexusVoxApp.__new__(NexusVoxApp)
    app._config = Config(inference=inference)
    app._device = "cuda"
    app._recording = False
    app._switch_status = "idle"
    app._switch_error = None
    old = MagicMock(needs_docker=True, disconnect=AsyncMock())
    app._transcriber = old

    async def run():
        await app.switch_model(model_id)

    asyncio.run(run())
    return app, docker, old


def test_switch_to_custom_server_stops_docker_and_starts_nothing(monkeypatch):
    inference = _custom()
    inference.model = "parakeet-tdt-0.6b"
    app, docker, _ = _switch("custom-server", inference, monkeypatch)

    assert app._switch_status == "ready"
    docker.stop_profile.assert_called_once_with("parakeet")
    docker.start_profile.assert_not_called()
    assert isinstance(app._transcriber, OpenAIHttpTranscriber)
    assert app._config.inference.server_url == InferenceConfig().server_url


def test_failed_switch_to_custom_server_keeps_the_old_model(monkeypatch):
    inference = _custom(url="")
    inference.model = "parakeet-tdt-0.6b"
    app, docker, _ = _switch("custom-server", inference, monkeypatch)

    assert app._switch_status == "error"
    assert "[inference.custom].url" in app._switch_error
    assert app._config.inference.model == "parakeet-tdt-0.6b"
    docker.start_profile.assert_called_once_with("parakeet")  # old container restored


# ---- Startup fallback ------------------------------------------------------


def _fallback(inference: InferenceConfig, device: str) -> InferenceConfig:
    try:
        from nexusvox.app import apply_startup_fallback
    except Exception:  # Windows-only imports on other platforms
        pytest.skip("nexusvox.app needs the Windows runtime")
    apply_startup_fallback(inference, device)
    return inference


def test_custom_server_without_url_starts_on_parakeet_with_its_url():
    inference = _custom(url="")
    inference.server_url = "ws://stale:8000/v1/realtime"
    _fallback(inference, "cuda")
    assert inference.model == "parakeet-tdt-0.6b"
    assert inference.server_url == "http://localhost:8002/v1/audio/transcriptions"


def test_custom_server_without_url_starts_on_whisper_on_cpu():
    assert _fallback(_custom(url=""), "cpu").model == "whisper-large-v3-turbo"


def test_custom_server_with_url_is_kept():
    assert _fallback(_custom(), "cuda").model == "custom-server"


def test_gpu_model_on_cpu_still_falls_back():
    assert _fallback(InferenceConfig(model="voxtral-mini-4b"), "cpu").model == "whisper-large-v3-turbo"
