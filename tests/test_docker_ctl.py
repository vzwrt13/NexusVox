"""Tests for Docker Compose orchestration module."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from nexusvox.docker_ctl import start_profile, stop_profile, wait_for_healthy


@patch("nexusvox.docker_ctl.subprocess.run")
def test_stop_profile_calls_docker_compose(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

    stop_profile("voxtral")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args == ["docker", "compose", "--profile", "voxtral", "stop"]


@patch("nexusvox.docker_ctl.subprocess.run")
def test_start_profile_calls_docker_compose(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

    start_profile("cohere")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args == ["docker", "compose", "--profile", "cohere", "up", "-d"]


@patch("nexusvox.docker_ctl.subprocess.run")
def test_stop_profile_raises_on_failure(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stderr="error msg")

    with pytest.raises(RuntimeError, match="Failed to stop"):
        stop_profile("voxtral")


@patch("nexusvox.docker_ctl.subprocess.run")
def test_start_profile_raises_on_failure(mock_run):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stderr="error msg")

    with pytest.raises(RuntimeError, match="Failed to start"):
        start_profile("cohere")


@patch("nexusvox.docker_ctl.time.sleep")
@patch("nexusvox.docker_ctl.httpx.Client")
def test_wait_for_healthy_returns_true_on_success(mock_client_cls, mock_sleep):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client.get.return_value = mock_response

    result = wait_for_healthy("http://localhost:8000/health", timeout=5.0)
    assert result is True


@patch("nexusvox.docker_ctl.time.sleep")
@patch("nexusvox.docker_ctl.time.monotonic")
@patch("nexusvox.docker_ctl.httpx.Client")
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


@patch("nexusvox.docker_ctl.subprocess.run")
def test_start_profile_raises_clear_error_when_docker_missing(mock_run):
    mock_run.side_effect = FileNotFoundError("docker not found")

    with pytest.raises(RuntimeError, match="Docker is not installed"):
        start_profile("voxtral")


@patch("nexusvox.docker_ctl.subprocess.run")
def test_stop_profile_raises_clear_error_when_docker_missing(mock_run):
    mock_run.side_effect = FileNotFoundError("docker not found")

    with pytest.raises(RuntimeError, match="Docker is not installed"):
        stop_profile("voxtral")
