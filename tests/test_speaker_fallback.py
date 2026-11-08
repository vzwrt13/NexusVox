"""A job that wants speaker detection runs on Parakeet when the active model cannot detect speakers."""

from unittest.mock import MagicMock

import pytest

from nexusvox.config import InferenceConfig
from nexusvox.transcription import speaker_fallback as sf


@pytest.fixture
def docker(monkeypatch):
    ctl = MagicMock()
    ctl.wait_for_healthy.return_value = True
    monkeypatch.setattr(sf, "docker_ctl", ctl)
    monkeypatch.setattr(sf, "_warm_up", lambda config: True)
    monkeypatch.setattr(sf, "resolve_device", lambda requested: "cuda")
    return ctl


def _config(model="whisper-large-v3-turbo", device="auto"):
    return InferenceConfig(model=model, device=device, server_url="http://localhost:8003/v1/audio/transcriptions")


def test_parakeet_active_is_unchanged(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: pytest.fail("no probe for Parakeet"))
    config = _config("parakeet-tdt-0.6b")
    with sf.speaker_detection_job(config, wanted=True) as job:
        assert job.config is config
        assert job.notice == ""
    docker.start_profile.assert_not_called()
    docker.stop_profile.assert_not_called()


def test_no_speakers_wanted_is_unchanged(docker):
    config = _config()
    with sf.speaker_detection_job(config, wanted=False) as job:
        assert job.config is config
    docker.start_profile.assert_not_called()


@pytest.mark.parametrize("model", ["whisper-large-v3-turbo", "custom-server"])
def test_incompatible_model_uses_running_container(docker, monkeypatch, model):
    monkeypatch.setattr(sf, "probe_server", lambda url: True)
    config = _config(model)
    with sf.speaker_detection_job(config, wanted=True, what="meeting") as job:
        assert job.config.model == "parakeet-tdt-0.6b"
        assert job.config.device == "cuda"  # pinned to the server: no cold CPU engine
        assert job.config.server_url.startswith("http://localhost:8002/")
        assert job.notice == "This meeting uses Parakeet for speaker detection."
    assert config.model == model  # dictation's config is untouched
    docker.start_profile.assert_not_called()
    docker.stop_profile.assert_not_called()  # the app did not start it


def test_container_started_and_stopped_again(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    with sf.speaker_detection_job(_config(), wanted=True) as job:
        assert job.started_container
        assert job.config.device == "cuda"
        docker.start_profile.assert_called_once_with("parakeet")
        docker.stop_profile.assert_not_called()  # still up while the job runs
    docker.stop_profile.assert_called_once_with("parakeet")


def test_container_stopped_when_the_job_raises(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    with pytest.raises(RuntimeError), sf.speaker_detection_job(_config(), wanted=True):
        raise RuntimeError("boom")
    docker.stop_profile.assert_called_once_with("parakeet")


def test_start_fails_falls_back_to_cpu(docker, monkeypatch, caplog):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    docker.start_profile.side_effect = RuntimeError("Docker is not installed")
    with sf.speaker_detection_job(_config(), wanted=True) as job:
        assert job.config.model == "parakeet-tdt-0.6b"
        assert job.config.device == "cpu"
        assert not job.started_container
    docker.stop_profile.assert_not_called()
    assert "Docker is not installed" in caplog.text


def test_unhealthy_container_is_stopped_and_cpu_is_used(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    docker.wait_for_healthy.return_value = False
    with sf.speaker_detection_job(_config(), wanted=True) as job:
        assert job.config.device == "cpu"
    docker.stop_profile.assert_called_once_with("parakeet")


def test_no_gpu_uses_cpu_without_touching_docker(docker, monkeypatch):
    monkeypatch.setattr(sf, "resolve_device", lambda requested: "cpu")
    with sf.speaker_detection_job(_config(device="cpu"), wanted=True) as job:
        assert job.config.model == "parakeet-tdt-0.6b"
        assert job.config.device == "cpu"
    docker.start_profile.assert_not_called()
