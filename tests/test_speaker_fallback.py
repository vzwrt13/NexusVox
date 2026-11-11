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
    warm = MagicMock()
    monkeypatch.setattr(sf, "_warm_up", warm)
    ctl.warm = warm
    monkeypatch.setattr(sf, "cuda_available", lambda: True)
    return ctl


def _config(model="whisper-large-v3-turbo", device="auto"):
    return InferenceConfig(model=model, device=device, server_url="http://localhost:8003/v1/audio/transcriptions")


def test_parakeet_active_is_unchanged(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: pytest.fail("no probe for Parakeet"))
    config = _config("parakeet-tdt-0.6b", device="cuda")
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
    docker.warm.assert_not_called()  # a running server needs no warm-up (it may be busy)
    docker.stop_profile.assert_not_called()  # the app did not start it


def test_container_started_warmed_and_stopped_again(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    with sf.speaker_detection_job(_config(), wanted=True) as job:
        assert job.started_container
        assert job.config.device == "cuda"
        docker.start_profile.assert_called_once_with("parakeet")
        docker.warm.assert_called_once()
        docker.stop_profile.assert_not_called()  # still up while the job runs
    docker.stop_profile.assert_called_once_with("parakeet")


def test_container_stopped_when_the_job_raises(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    with pytest.raises(RuntimeError), sf.speaker_detection_job(_config(), wanted=True):
        raise RuntimeError("boom")
    docker.stop_profile.assert_called_once_with("parakeet")


def test_container_kept_when_dictation_switched_to_parakeet(docker, monkeypatch):
    answers = iter([False, True])  # down when the job starts, up (the job started it) when it ends
    monkeypatch.setattr(sf, "probe_server", lambda url: next(answers))
    with sf.speaker_detection_job(_config(), wanted=True, active_model=lambda: "parakeet-tdt-0.6b"):
        pass
    docker.start_profile.assert_called_once()
    docker.stop_profile.assert_not_called()  # now dictation's server


def test_container_stopped_when_dictation_stays_on_another_model(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    with sf.speaker_detection_job(_config(), wanted=True, active_model=lambda: "whisper-large-v3-turbo"):
        pass
    docker.stop_profile.assert_called_once_with("parakeet")


def test_start_fails_keeps_the_active_model(docker, monkeypatch, caplog):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    docker.start_profile.side_effect = RuntimeError("Docker is not installed")
    config = _config()
    with sf.speaker_detection_job(config, wanted=True) as job:
        assert job.config is config
        assert "Docker is not installed" in job.notice
        assert "no speaker labels" in job.notice
    assert "Docker is not installed" in caplog.text
    # compose may have created the container before it failed: it is stopped, the job started it
    docker.stop_profile.assert_called_once_with("parakeet")


def test_health_check_raising_still_stops_the_container(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    docker.wait_for_healthy.side_effect = OSError("protocol error")
    config = _config()
    with sf.speaker_detection_job(config, wanted=True) as job:
        assert job.config is config
    docker.stop_profile.assert_called_once_with("parakeet")


def test_unhealthy_container_is_stopped_and_the_model_kept(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    docker.wait_for_healthy.return_value = False
    config = _config()
    with sf.speaker_detection_job(config, wanted=True) as job:
        assert job.config is config
    docker.stop_profile.assert_called_once_with("parakeet")


def test_no_gpu_keeps_the_active_model_without_touching_docker(docker, monkeypatch):
    monkeypatch.setattr(sf, "cuda_available", lambda: False)
    config = _config(device="cpu")
    with sf.speaker_detection_job(config, wanted=True) as job:
        assert job.config is config
        assert "no NVIDIA GPU" in job.notice
    docker.start_profile.assert_not_called()


def test_gpu_is_found_whatever_the_models_device_setting(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: True)
    with sf.speaker_detection_job(_config(device="cpu"), wanted=True) as job:
        assert job.config.model == "parakeet-tdt-0.6b"


def test_a_failed_fallback_is_marked_so_the_job_adds_no_second_note(docker, monkeypatch):
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    docker.wait_for_healthy.return_value = False
    with sf.speaker_detection_job(_config(), wanted=True) as job:
        assert job.failed
    monkeypatch.setattr(sf, "probe_server", lambda url: True)
    with sf.speaker_detection_job(_config(), wanted=True) as job:
        assert not job.failed


def test_parakeet_on_the_cpu_takes_the_container_path(docker, monkeypatch):
    """The registry flag is not enough: Parakeet pinned to the CPU cannot detect speakers."""
    monkeypatch.setattr(sf, "probe_server", lambda url: True)
    with sf.speaker_detection_job(_config("parakeet-tdt-0.6b", device="cpu"), wanted=True, what="upload") as job:
        assert job.config.model == "parakeet-tdt-0.6b"
        assert job.config.device == "cuda"
        assert job.notice == "This upload uses Parakeet for speaker detection."


def test_auto_parakeet_with_the_server_down_starts_the_container(docker, monkeypatch):
    answers = iter([False, False, True])  # engine check, availability check, exit check
    monkeypatch.setattr(sf, "probe_server", lambda url: next(answers, True))
    with sf.speaker_detection_job(
        _config("parakeet-tdt-0.6b"), wanted=True, active_model=lambda: "whisper-large-v3-turbo"
    ) as job:
        assert job.started_container is True
    docker.start_profile.assert_called_once()
    docker.stop_profile.assert_called_once_with("parakeet")


def test_container_stopped_when_dictation_runs_parakeet_on_the_cpu(docker, monkeypatch):
    """A started container is not dictation's server when dictation uses CPU Parakeet: its VRAM is released."""
    monkeypatch.setattr(sf, "probe_server", lambda url: False)
    with sf.speaker_detection_job(
        _config("whisper-large-v3-turbo", device="cpu"), wanted=True, active_model=lambda: "parakeet-tdt-0.6b"
    ):
        pass
    docker.start_profile.assert_called_once()
    docker.stop_profile.assert_called_once_with("parakeet")
