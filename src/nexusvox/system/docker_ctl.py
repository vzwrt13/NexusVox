"""Docker Compose orchestration for switching between model containers."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from .paths import DOCKER_UNAVAILABLE_MSG, is_frozen

logger = logging.getLogger("nexusvox.docker_ctl")

DOCKER_DIR = Path(__file__).resolve().parents[3] / "docker"

_MISSING_DOCKER_MSG = (
    "Docker is not installed or not in PATH. CPU models (Whisper) do not need "
    "Docker — select a CPU-capable model in the dashboard Settings tab."
)


def _run_compose(args: list[str]) -> subprocess.CompletedProcess[str]:
    if is_frozen():
        # The compose files are not shipped in the installed app.
        raise RuntimeError(DOCKER_UNAVAILABLE_MSG)
    try:
        return subprocess.run(
            ["docker", "compose", *args],
            cwd=DOCKER_DIR,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(_MISSING_DOCKER_MSG) from exc


def unavailable_reason() -> str | None:
    """Why Docker cannot be used right now, as a short phrase, or None when the daemon answers.

    Only "Docker is out of reach" counts: a running Docker whose compose run fails (port in use, image
    error) is a real error and stays one.
    """
    if is_frozen():
        return "Docker is not available in the installed app"
    if shutil.which("docker") is None:
        return "Docker is not installed"
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return "Docker is not running"
    return None if result.returncode == 0 else "Docker is not running"


def stop_profile(profile: str) -> None:
    """Stop containers for a Docker Compose profile."""
    logger.info("Stopping Docker profile: %s", profile)
    result = _run_compose(["--profile", profile, "stop"])
    if result.returncode != 0:
        logger.error("docker compose stop failed: %s", result.stderr)
        raise RuntimeError(f"Failed to stop profile {profile}: {result.stderr}")
    logger.info("Stopped Docker profile: %s", profile)


def start_profile(profile: str) -> None:
    """Start containers for a Docker Compose profile (detached)."""
    logger.info("Starting Docker profile: %s", profile)
    result = _run_compose(["--profile", profile, "up", "-d"])
    if result.returncode != 0:
        logger.error("docker compose up failed: %s", result.stderr)
        raise RuntimeError(f"Failed to start profile {profile}: {result.stderr}")
    logger.info("Started Docker profile: %s", profile)


def wait_for_healthy(url: str, timeout: float = 120.0, interval: float = 2.0) -> bool:
    """Poll a health endpoint until it returns HTTP 200 or timeout expires."""
    logger.info("Waiting for %s to become healthy (timeout=%.0fs)", url, timeout)
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=3.0) as client:
        while time.monotonic() < deadline:
            try:
                resp = client.get(url)
                if resp.status_code == 200:
                    logger.info("Health check passed: %s", url)
                    return True
            except httpx.TransportError:
                # Refused, reset, disconnected without a reply, timed out: a container that is still loading its
                # model drops connections like this. Only the deadline ends the wait.
                pass
            time.sleep(interval)
    logger.warning("Health check timed out after %.0fs: %s", timeout, url)
    return False
