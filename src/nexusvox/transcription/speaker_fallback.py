"""Speaker detection for a meeting or an upload, whatever model dictation uses.

Only Parakeet on its GPU container detects speakers. A job that wants them while another model is active
(Whisper, a custom server) runs its speaker-detected audio on Parakeet instead: `speaker_detection_job` picks the
config for it, starts the Parakeet container when it is not running (and stops it again afterwards, only when it
started it), and returns a short notice for the user. Dictation never sees any of this: it keeps the active model,
and the job's config is a copy.

When the container cannot be had (no NVIDIA GPU, Docker missing, the installed app, not enough VRAM) the job keeps
the user's own model: Parakeet on the CPU cannot detect speakers either, so switching would only change the
language coverage. The reason is logged and shown in the notice, and the transcript has no speaker labels.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

from ..config import DEFAULT_MODEL, MODEL_REGISTRY, InferenceConfig, cuda_available
from ..system import docker_ctl
from ..transcribers.auto_parakeet import health_url_for, probe_server, warm_up_server
from .file_transcribe import NO_SPEAKER_LABELS

logger = logging.getLogger(__name__)

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


def _warm_up(config: InferenceConfig) -> None:
    """Load the model of a server this job just started (~17 s); best effort, the detection request has its own wait."""
    try:
        if not asyncio.run(warm_up_server(config)):
            logger.warning("Warming up the Parakeet server returned no result")
    except Exception as exc:
        logger.warning("Warming up the Parakeet server failed: %s", exc)


def _stop(profile: str) -> None:
    try:
        docker_ctl.stop_profile(profile)
    except Exception as exc:
        logger.warning("Could not stop the Parakeet container the job started: %s", exc)


def _start(config: InferenceConfig, profile: str, started: list[bool]) -> bool:
    """Start the container and wait for it; `started` is set before anything can fail, so it is always stopped."""
    started.append(True)
    docker_ctl.start_profile(profile)
    if not docker_ctl.wait_for_healthy(health_url_for(config), timeout=HEALTH_TIMEOUT_S):
        return False
    _warm_up(config)
    return True


def _unavailable(config: InferenceConfig, why: str, what: str) -> SpeakerJob:
    logger.warning("No speaker detection for the %s: %s", what, why)
    return SpeakerJob(config, f"Speaker detection needs the Parakeet GPU container ({why}); {NO_SPEAKER_LABELS}")


@contextmanager
def speaker_detection_job(
    config: InferenceConfig,
    *,
    wanted: bool,
    what: str = "job",
    active_model: Callable[[], str] | None = None,
) -> Iterator[SpeakerJob]:
    """Settle which model audio that wants speaker detection is transcribed with; blocking.

    Unchanged (the active `config`) when the job does not want speakers or the active model detects them. Else
    Parakeet on its GPU container, when it answers or can be started (stopped again on exit if started here), or
    the unchanged `config` with a notice saying why not. `what` ("meeting", "upload") goes into the notice.
    `active_model()` names the dictation model at exit: when the user switched to Parakeet meanwhile, the
    container is now dictation's server and stays up.
    """
    if not wanted or supports_speaker_detection(config):
        yield SpeakerJob(config)
        return

    info = MODEL_REGISTRY[DEFAULT_MODEL]
    profile = str(info["docker_profile"])
    # Pinned to the server: it is up and warm, the cold CPU engine of `AutoParakeetTranscriber` is not wanted.
    gpu = replace(config, model=DEFAULT_MODEL, server_url=str(info["default_url"]), compute_type=None, device="cuda")
    started: list[bool] = []
    try:
        if not cuda_available():  # the hardware, not the active model's device setting
            yield _unavailable(config, "no NVIDIA GPU", what)
            return
        reason = "it did not become healthy"
        try:
            ready = probe_server(health_url_for(gpu)) or _start(gpu, profile, started)
        except Exception as exc:
            ready, reason = False, str(exc) or type(exc).__name__
        if not ready:
            yield _unavailable(config, reason, what)
            return
        logger.info("The %s uses the Parakeet container for speaker detection", what)
        yield SpeakerJob(gpu, f"This {what} uses Parakeet for speaker detection.", bool(started))
    finally:
        if started:
            switched = active_model is not None and supports_speaker_detection(replace(config, model=active_model()))
            if switched:
                logger.info("Dictation switched to Parakeet during the job: its container stays up")
            else:
                _stop(profile)
