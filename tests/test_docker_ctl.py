"""Tests for Docker Compose orchestration module."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from nexusvox.system.docker_ctl import DOCKER_DIR, start_profile, stop_profile, unavailable_reason, wait_for_healthy


def test_docker_dir_finds_the_compose_file():
    """The path is counted up from the module's own folder, so moving the module breaks it without an error."""
    assert (DOCKER_DIR / "docker-compose.yml").is_file()


@patch("nexusvox.system.docker_ctl.subprocess.run")
def test_stop_profile_calls_docker_compose(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

    stop_profile("voxtral")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args == ["docker", "compose", "--profile", "voxtral", "stop"]


@patch("nexusvox.system.docker_ctl.subprocess.run")
def test_start_profile_calls_docker_compose(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

    start_profile("cohere")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args == ["docker", "compose", "--profile", "cohere", "up", "-d"]


@patch("nexusvox.system.docker_ctl.subprocess.run")
def test_stop_profile_raises_on_failure(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stderr="error msg")

    with pytest.raises(RuntimeError, match="Failed to stop"):
        stop_profile("voxtral")


@patch("nexusvox.system.docker_ctl.subprocess.run")
def test_start_profile_raises_on_failure(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stderr="error msg")

    with pytest.raises(RuntimeError, match="Failed to start"):
        start_profile("cohere")


@patch("nexusvox.system.docker_ctl.time.sleep")
@patch("nexusvox.system.docker_ctl.httpx.Client")
def test_wait_for_healthy_returns_true_on_success(mock_client_cls, mock_sleep):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client.get.return_value = mock_response

    result = wait_for_healthy("http://localhost:8000/health", timeout=5.0)
    assert result is True


@patch("nexusvox.system.docker_ctl.time.sleep")
@patch("nexusvox.system.docker_ctl.time.monotonic")
@patch("nexusvox.system.docker_ctl.httpx.Client")
def test_wait_for_healthy_returns_false_on_timeout(mock_client_cls, mock_monotonic, mock_sleep):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

    import httpx

    mock_client.get.side_effect = httpx.ConnectError("refused")
    # Simulate time passing beyond the timeout
    mock_monotonic.side_effect = [0.0, 0.0, 130.0]

    result = wait_for_healthy("http://localhost:8000/health", timeout=120.0)
    assert result is False


@patch("nexusvox.system.docker_ctl.time.sleep")
@patch("nexusvox.system.docker_ctl.httpx.Client")
def test_wait_for_healthy_retries_connection_errors_until_it_answers(mock_client_cls, mock_sleep):
    import httpx

    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
    ok = MagicMock()
    ok.status_code = 200
    # A container that is still loading its model refuses, resets or drops the connection before it answers.
    mock_client.get.side_effect = [
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ConnectError("refused"),
        httpx.ReadError("reset"),
        httpx.ConnectTimeout("slow"),
        ok,
    ]

    assert wait_for_healthy("http://localhost:8000/health", timeout=120.0) is True
    assert mock_client.get.call_count == 5


@patch("nexusvox.system.docker_ctl.time.sleep")
@patch("nexusvox.system.docker_ctl.time.monotonic")
@patch("nexusvox.system.docker_ctl.httpx.Client")
def test_wait_for_healthy_gives_up_on_disconnects_only_at_the_deadline(mock_client_cls, mock_monotonic, mock_sleep):
    import httpx

    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = httpx.RemoteProtocolError("Server disconnected without sending a response.")
    mock_monotonic.side_effect = [0.0, 0.0, 1.0, 2.0, 130.0]

    assert wait_for_healthy("http://localhost:8000/health", timeout=120.0) is False
    assert mock_client.get.call_count == 3


@patch("nexusvox.system.docker_ctl.subprocess.run")
def test_start_profile_raises_clear_error_when_docker_missing(mock_run):
    mock_run.side_effect = FileNotFoundError("docker not found")

    with pytest.raises(RuntimeError, match="Docker is not installed"):
        start_profile("voxtral")


@patch("nexusvox.system.docker_ctl.subprocess.run")
def test_stop_profile_raises_clear_error_when_docker_missing(mock_run):
    mock_run.side_effect = FileNotFoundError("docker not found")

    with pytest.raises(RuntimeError, match="Docker is not installed"):
        stop_profile("voxtral")


@pytest.mark.parametrize(
    ("frozen", "which", "run", "expected"),
    [
        (True, "docker", 0, "installed app"),
        (False, None, 0, "not installed"),
        (False, "docker", 1, "not running"),
        (False, "docker", subprocess.TimeoutExpired("docker", 15), "not running"),
        (False, "docker", 0, None),
    ],
)
def test_unavailable_reason(frozen, which, run, expected):
    result = subprocess.CompletedProcess(args=[], returncode=run) if isinstance(run, int) else None
    with (
        patch("nexusvox.system.docker_ctl.is_frozen", return_value=frozen),
        patch("nexusvox.system.docker_ctl.shutil.which", return_value=which),
        patch("nexusvox.system.docker_ctl.subprocess.run", return_value=result, side_effect=None if result else run),
    ):
        reason = unavailable_reason()
    assert (reason is None) if expected is None else (expected in reason)
