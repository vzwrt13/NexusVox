"""Docker Compose orchestration for switching between model containers."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DOCKER_DIR = Path(__file__).resolve().parent.parent.parent / "docker"

_MISSING_DOCKER_MSG = (
    "Docker is not installed or not in PATH. CPU models (Whisper) do not need "
    "Docker — select a CPU-capable model in the dashboard Settings tab."
)


def _run_compose(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["docker", "compose", *args],
            cwd=DOCKER_DIR,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(_MISSING_DOCKER_MSG) from exc


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
            except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
                pass
            time.sleep(interval)
    logger.warning("Health check timed out after %.0fs: %s", timeout, url)
    return False
