"""Speaker detection for a meeting or an upload, whatever model dictation uses.

Only Parakeet on its GPU container detects speakers. A job that wants them while another model is active
(Whisper, a custom server) runs on Parakeet instead: `speaker_detection_job` picks the config the job
transcribes with, starts the Parakeet container when it is not running (and stops it again afterwards, only
when it started it), and returns a short notice for the user. Dictation never sees any of this: it keeps the
active model, and the job's config is a copy.

When the container cannot be had (no NVIDIA GPU, Docker missing, the installed app, not enough VRAM) the job
still transcribes, on CPU Parakeet without speakers; the reason is logged and the job's own "speaker detection
needs the GPU container" note says so in the transcript's notes. A job never fails because of the active model.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

from ..config import DEFAULT_MODEL, MODEL_REGISTRY, InferenceConfig, resolve_device
from ..system import docker_ctl
from ..transcribers.auto_parakeet import health_url_for, probe_server
from .transcriber import OpenAIHttpTranscriber

logger = logging.getLogger(__name__)

# The server loads its model on the first request (~17 s); the warm-up request waits for that.
_WARM_UP_SILENCE_S = 0.5
_WARM_UP_TIMEOUT_S = 90.0
HEALTH_TIMEOUT_S = 120.0


@dataclass
class SpeakerJob:
    """What a job transcribes with: `config`, and the `notice` to show the user (empty when nothing changed)."""

    config: InferenceConfig
    notice: str = ""
    started_container: bool = False


def supports_speaker_detection(config: InferenceConfig) -> bool:
    """Whether the active model detects speakers by itself (registry flag `diarization`)."""
    return bool(MODEL_REGISTRY.get(config.model, {}).get("diarization"))


def _warm_up(config: InferenceConfig) -> bool:
    async def run() -> bool:
        client = OpenAIHttpTranscriber(config)
        await client.connect()
        await client.send_audio_chunk(bytes(int(16_000 * _WARM_UP_SILENCE_S) * 2))
        return (await client.finish(timeout=_WARM_UP_TIMEOUT_S)).done

    try:
        return asyncio.run(run())
    except Exception as exc:
        logger.warning("Warming up the Parakeet server failed: %s", exc)
        return False


def _ensure_server(config: InferenceConfig, profile: str) -> tuple[bool, bool]:
    """(server answers and is warm, this call started it). Raises when the container cannot be started."""
    health_url = health_url_for(config)
    started = False
    if not probe_server(health_url):
        docker_ctl.start_profile(profile)
        started = True
        if not docker_ctl.wait_for_healthy(health_url, timeout=HEALTH_TIMEOUT_S):
            return False, started
    return _warm_up(config), started


def _stop(profile: str) -> None:
    try:
        docker_ctl.stop_profile(profile)
    except Exception as exc:
        logger.warning("Could not stop the Parakeet container the job started: %s", exc)


@contextmanager
def speaker_detection_job(config: InferenceConfig, *, wanted: bool, what: str = "job") -> Iterator[SpeakerJob]:
    """Settle which model a job that wants speaker detection transcribes with; blocking.

    Unchanged (the active `config`) when the job does not want speakers or the active model detects them. Else
    Parakeet: on the GPU container when it answers or can be started (stopped again on exit if started here),
    on CPU without speakers when not. `what` ("meeting", "upload") goes into the notice.
    """
    if not wanted or supports_speaker_detection(config):
        yield SpeakerJob(config)
        return

    info = MODEL_REGISTRY[DEFAULT_MODEL]
    parakeet = replace(
        config, model=DEFAULT_MODEL, server_url=str(info["default_url"]), compute_type=None, device="cpu"
    )
    notice = f"This {what} uses Parakeet for speaker detection."
    started = False
    profile = str(info["docker_profile"])
    try:
        if resolve_device(config.device) != "cuda":
            logger.info("No NVIDIA GPU: the %s uses CPU Parakeet without speaker detection", what)
            yield SpeakerJob(parakeet, notice)
            return
        gpu = replace(parakeet, device="cuda")  # pinned: the server is up and warm, the CPU engine is not wanted
        try:
            ready, started = _ensure_server(gpu, profile)
        except Exception as exc:
            ready = False
            logger.warning("Could not start the Parakeet container for the %s: %s", what, exc)
        if ready:
            logger.info("The %s uses the Parakeet container for speaker detection", what)
            yield SpeakerJob(gpu, notice, started)
        else:
            logger.warning("The Parakeet container is not usable: the %s uses CPU Parakeet without speakers", what)
            yield SpeakerJob(parakeet, notice, started)
    finally:
        if started:
            _stop(profile)
