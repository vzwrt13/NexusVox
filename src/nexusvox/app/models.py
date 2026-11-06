"""Which transcription model runs: the startup fallback, switching at runtime and the warm-up."""

from __future__ import annotations

import asyncio

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

    _switch_reset: asyncio.Future | None = None  # puts the status back to idle after a finished switch

    def get_switch_status(self) -> dict:
        """Return the current model-switch status for the dashboard.

        Once no switch is in flight (idle, or just ready), an in-process model that is still downloading
        or loading reports that (`downloading` / `loading`), and one that failed to load reports `error`
        with the reason, so the first start of CPU Parakeet neither looks done nor hung.
        """
        if self._switch_status in ("idle", "ready"):
            load = self._transcriber.load_status()
            if load and load["state"] in ("downloading", "loading"):
                return {"status": load["state"], "error": None}
            if load and load["state"] == "error":
                return {"status": "error", "error": load["error"]}
        return {"status": self._switch_status, "error": self._switch_error}

    async def switch_model(self, model_id: str) -> None:
        """Switch to a different transcription model.

        In-process (Parakeet or Whisper on CPU) and custom-server switches skip Docker entirely;
        GPU-backed switches stop the old container, start the new one, and wait for health.
        """
        if model_id not in MODEL_REGISTRY:
            raise ValueError(f"Unknown model: {model_id}")
        if self._switch_status not in ("idle", "ready", "error"):
            raise RuntimeError("Model switch already in progress")

        old_model = self._config.inference.model
        if old_model == model_id:
            return
        if self._cycle_running:
            # The cycle holds the live transcriber. The dashboard reads the refusal from the switch status.
            self._switch_status = "error"
            self._switch_error = "Cannot switch the model during a dictation"
            raise RuntimeError(self._switch_error)
        old_url = self._config.inference.server_url

        new_info = MODEL_REGISTRY[model_id]
        old_info = MODEL_REGISTRY[old_model]
        loop = asyncio.get_event_loop()

        new_needs_docker = model_needs_docker(model_id, self._device)
        old_was_docker = self._transcriber.needs_docker
        if new_needs_docker and is_frozen():
            raise RuntimeError(DOCKER_UNAVAILABLE_MSG)

        if self._switch_reset is not None:
            self._switch_reset.cancel()  # its 5 s are for the last switch, not for this one
            self._switch_reset = None

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
                    raise RuntimeError(f"Container for {model_id} did not become healthy")

            self._config.inference.model = model_id
            if new_needs_docker:
                self._config.inference.server_url = str(new_info["default_url"])
            self._transcriber = create_transcriber(
                self._config.inference,
                device=self._device,
                auto_fallback=self._config.inference.device == "auto",
            )
            save_config(self._config)

            self._switch_status = "ready"
            logger.info("Switched to model: %s", model_id)
            if self._warms_up():
                asyncio.create_task(self._preload_transcriber())

            async def _reset_status():
                await asyncio.sleep(5)
                self._switch_status = "idle"
                self._switch_error = None

            self._switch_reset = asyncio.ensure_future(_reset_status())

        except Exception as exc:
            self._switch_status = "error"
            self._switch_error = str(exc)
            logger.exception("Model switch failed")
            # Keep the config on the model that is actually serving.
            self._config.inference.model = old_model
            self._config.inference.server_url = old_url
            # Fallback: restart the old container if it was Docker-backed.
            try:
                if old_was_docker:
                    await loop.run_in_executor(None, docker_ctl.start_profile, old_info["docker_profile"])
                    await loop.run_in_executor(None, docker_ctl.wait_for_healthy, old_info["health_url"])
                self._transcriber = create_transcriber(
                    self._config.inference,
                    device=self._device,
                    auto_fallback=self._config.inference.device == "auto",
                )
            except Exception:
                logger.exception("Fallback restart also failed")

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
