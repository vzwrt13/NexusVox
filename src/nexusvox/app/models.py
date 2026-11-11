"""Which transcription model runs: the startup fallback, switching at runtime and the warm-up."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from ..config import DEFAULT_MODEL, MODEL_REGISTRY, InferenceConfig, model_needs_docker, save_config
from ..system import docker_ctl
from ..system.paths import DOCKER_UNAVAILABLE_MSG, is_frozen
from ..transcription.transcriber import create_transcriber
from .common import logger


def apply_startup_fallback(inference: InferenceConfig, device: str) -> None:
    """Swap a model that cannot start for Parakeet, for this session.

    The custom-server slot without a URL cannot build a client, so it falls back instead of crashing
    before the dashboard is up. The TOML is not rewritten here.
    """
    info = MODEL_REGISTRY.get(inference.model, {})
    if not (info.get("custom") and not inference.custom.url):
        return
    fallback = DEFAULT_MODEL
    logger.warning(
        "Configured model %s needs [inference.custom].url in config.toml. Falling back to %s for this session.",
        inference.model,
        fallback,
    )
    inference.model = fallback
    if model_needs_docker(fallback, device):
        inference.server_url = str(MODEL_REGISTRY[fallback]["default_url"])


class ModelSwitching:
    """The model part of `NexusVoxApp`."""

    def get_switch_status(self) -> dict:
        """Return the current model-switch status for the dashboard.

        Once no switch is in flight (idle, or just ready), an in-process model that is still downloading
        or loading reports that (`downloading` / `loading`), and one that failed to load reports `error`
        with the reason, so the first start of CPU Parakeet neither looks done nor hung.
        """
        if self._switch_status in ("idle", "ready"):
            load = self._transcriber.load_status()
            if load and load["state"] in ("downloading", "loading"):
                status = {"status": load["state"], "error": None}
                if self._switch_note:  # the first CPU start downloads: the reason must not vanish meanwhile
                    status["note"] = self._switch_note
                return status
            if load and load["state"] == "error":
                return {"status": "error", "error": load["error"]}
        status = {"status": self._switch_status, "error": self._switch_error}
        if self._switch_note and self._switch_status == "ready":
            status["note"] = self._switch_note
        return status

    def _switch_in_progress(self) -> bool:
        """Whether a model switch is tearing down or building the transcriber right now."""
        return self._switch_status not in ("idle", "ready", "error")

    def _build_transcriber(self):
        """The transcriber for `[inference]` as it stands, on the resolved device."""
        inference = self._config.inference
        return create_transcriber(inference, device=self._device, auto_fallback=inference.device == "auto")

    def _cancel_switch_reset(self) -> None:
        """Stop the timer of the last switch: its 5 s are for that switch, not for the next one."""
        if self._switch_reset is not None:
            self._switch_reset.cancel()
            self._switch_reset = None

    def _end_switch(self, status: str, error: str | None = None) -> None:
        """Settle the switch status ("ready" or "error"); the dashboard reads it for 5 s, then it goes back to idle."""
        self._switch_status = status
        self._switch_error = error
        self._cancel_switch_reset()

        async def _reset_status():
            await asyncio.sleep(5)
            self._switch_status = "idle"
            self._switch_error = None

        self._switch_reset = asyncio.ensure_future(_reset_status())

    async def switch_model(self, model_id: str) -> None:
        """Switch to a different transcription model.

        In-process (Parakeet or Whisper on CPU) and custom-server switches skip Docker entirely;
        GPU-backed switches stop the old container, start the new one, and wait for health.
        The switch holds `_transcriber_lock` throughout, as a dictation cycle does for its take: a switch is
        refused during a dictation, and a press during a switch records nothing (`_take_during_switch`).
        """
        if model_id not in MODEL_REGISTRY:
            raise ValueError(f"Unknown model: {model_id}")
        if self._switch_in_progress():
            raise RuntimeError("Model switch already in progress")

        old_model = self._config.inference.model
        if old_model == model_id:
            return
        self._switch_note = None  # a note belongs to the switch that set it
        if self._transcriber_lock.locked():
            # A dictation holds the live transcriber. The dashboard reads the refusal from the switch status.
            self._end_switch("error", "Cannot switch the model during a dictation")
            raise RuntimeError(self._switch_error)
        old_url = self._config.inference.server_url

        new_info = MODEL_REGISTRY[model_id]
        old_info = MODEL_REGISTRY[old_model]
        loop = asyncio.get_event_loop()

        new_needs_docker = model_needs_docker(model_id, self._device)
        old_was_docker = self._transcriber.needs_docker
        # "auto" may move a model that has an in-process path to the CPU when Docker is out of reach;
        # an explicit device is never overridden.
        cpu_fallback_ok = bool(new_info.get("inprocess_supported")) and self._config.inference.device == "auto"
        if new_needs_docker and is_frozen() and not cpu_fallback_ok:
            raise RuntimeError(DOCKER_UNAVAILABLE_MSG)
        # Set once the switch runs on the CPU although Docker was wanted. Checked before the old container
        # is stopped: with Docker out of reach `compose stop` would fail first.
        cpu_reason = docker_ctl.unavailable_reason() if new_needs_docker and cpu_fallback_ok else None
        if cpu_reason:
            new_needs_docker = False
            old_was_docker = False  # nothing of ours can be running without Docker

        self._cancel_switch_reset()
        replaced = False  # whether `self._transcriber` already serves the new model
        # Free (checked above, and nothing was awaited since), so this takes it at once.
        async with self._transcriber_lock:
            try:
                self._switch_status = "stopping"
                await self._transcriber.disconnect()

                if old_was_docker:
                    await loop.run_in_executor(None, docker_ctl.stop_profile, old_info["docker_profile"])

                if new_needs_docker:
                    self._switch_status = "starting"
                    await loop.run_in_executor(None, docker_ctl.start_profile, new_info["docker_profile"])
                    self._switch_status = "waiting"
                    healthy = await loop.run_in_executor(None, docker_ctl.wait_for_healthy, new_info["health_url"])
                    if not healthy:
                        if not cpu_fallback_ok:
                            raise RuntimeError(f"Container for {model_id} did not become healthy")
                        cpu_reason = "The Docker container did not become healthy"
                        new_needs_docker = False
                        try:  # do not leave a half-started container behind
                            await loop.run_in_executor(None, docker_ctl.stop_profile, new_info["docker_profile"])
                        except Exception:
                            logger.info("Could not stop the unhealthy %s container", model_id)

                self._config.inference.model = model_id
                if new_needs_docker:
                    self._config.inference.server_url = str(new_info["default_url"])
                if cpu_reason:
                    self._switch_note = f"{cpu_reason}, using the CPU"
                    logger.info("Model %s runs on the CPU: %s", model_id, cpu_reason)
                    self._transcriber = self._create_cpu_fallback_transcriber()
                else:
                    self._transcriber = self._build_transcriber()
                replaced = True
                # The last step that can fail: once the file names the new model the switch stands, so memory
                # and file never disagree on the model.
                save_config(self._config)
                self._end_switch("ready")

            except Exception as exc:
                self._switch_note = None
                logger.exception("Model switch failed")
                # Keep the config on the model that is actually serving.
                self._config.inference.model = old_model
                self._config.inference.server_url = old_url
                # Fallback: restart the old container if it was Docker-backed. The old transcriber was only
                # disconnected and serves again; it is rebuilt only when the new one had already replaced it.
                # The lock stays held meanwhile, so no dictation starts against a transcriber about to change.
                try:
                    if old_was_docker:
                        await loop.run_in_executor(None, docker_ctl.start_profile, old_info["docker_profile"])
                        await loop.run_in_executor(None, docker_ctl.wait_for_healthy, old_info["health_url"])
                    if replaced:
                        self._transcriber = self._build_transcriber()
                        if self._warms_up():  # else the next dictation pays the cold load
                            self._spawn(self._preload_transcriber())
                except Exception:
                    logger.exception("Fallback restart also failed")
                self._end_switch("error", str(exc))
                return

            finally:
                if self._switch_in_progress():
                    # Cancelled, or a BaseException out of Docker: neither branch above settled the status, and
                    # one left at "waiting" would refuse every later switch.
                    self._end_switch("error", "The model switch was interrupted")

        logger.info("Switched to model: %s", model_id)
        if self._warms_up():
            self._spawn(self._preload_transcriber())

    def _create_cpu_fallback_transcriber(self):
        """The transcriber for a switch that Docker could not serve.

        Parakeet from source keeps the "auto" engine: it runs on the CPU now and returns to the server once
        one answers. Otherwise (installed app, Whisper) it is plain CPU, with a GPU compute-type override dropped.
        """
        inference = self._config.inference
        if not is_frozen() and MODEL_REGISTRY[inference.model].get("inprocess_engine") == "parakeet-onnx":
            return create_transcriber(inference, device=self._device, auto_fallback=True)
        return create_transcriber(replace(inference, compute_type=None), device="cpu")

    def _is_inprocess(self) -> bool:
        """Whether the active model runs in this process; a custom server is remote and has nothing to load."""
        return not self._transcriber.needs_docker and not MODEL_REGISTRY[self._config.inference.model].get("custom")

    def _warms_up(self) -> bool:
        """Whether the active engine is warmed up in the background: in-process models load, "auto" warms the server."""
        return self._is_inprocess() or self._transcriber.warms_up_at_start

    async def _preload_transcriber(self) -> None:
        """Warm up an in-process transcriber so the first hotkey press is snappy."""
        try:
            logger.info("Preloading in-process transcriber model (first run may download)")
            await self._transcriber.warm_up()
            logger.info("Preload complete")
        except Exception:
            logger.exception("Preloading transcriber failed")
