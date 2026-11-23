"""Dashboard API: transcription models and the compute device."""

from __future__ import annotations

import time

from ..config import (
    CUSTOM_PROTOCOLS,
    MODEL_REGISTRY,
    cuda_available,
    model_download_state,
    model_needs_docker,
    resolve_device,
    save_config,
)
from ..system.paths import models_dir
from ..transcription import model_download
from ..transcription.diarization import speaker_detection_models
from .api_base import DashboardState, logger

# How long a model's downloaded-or-not answer is reused (seconds).
DOWNLOAD_STATE_TTL_S = 10.0


class ModelsMixin(DashboardState):
    """The model list, model switching and the compute device."""

    # ---- Models -------------------------------------------------------------

    def _download_state(self, model_id: str, resolved: str) -> dict:
        """`downloaded`/`size` of the in-process copy; no verdict when the model runs in a container.

        On a GPU the model lives in the container's own volume, which this process cannot see, so a mark
        from the local cache would mislead. The cache probe is kept for a few seconds (every dashboard load
        asks) and dropped when a model finishes loading (`get_model_status`).
        """
        if MODEL_REGISTRY[model_id].get("custom") or model_needs_docker(model_id, resolved):
            return {"downloaded": None, "size": ""}
        cached = self._download_cache.get(model_id)
        if cached and time.monotonic() - cached[0] < DOWNLOAD_STATE_TTL_S:
            return cached[1]
        state = model_download_state(model_id, models_dir())
        self._download_cache[model_id] = (time.monotonic(), state)
        return state

    def get_available_models(self) -> list[dict]:
        custom = self._config.inference.custom
        resolved = resolve_device(self._config.inference.device)
        return [
            {
                "id": model_id,
                "name": info["display_name"],
                # A custom server reports the protocol it is configured to speak.
                "protocol": CUSTOM_PROTOCOLS[custom.protocol] if info.get("custom") else info["protocol"],
                "custom": bool(info.get("custom", False)),
                "server_url": custom.url if info.get("custom") else "",
                "description": info.get("description", ""),
                "parameters": info.get("parameters", "—"),
                "architecture": info.get("architecture", "—"),
                "languages": info.get("languages", "—"),
                "streaming": custom.protocol == "realtime"
                if info.get("custom")
                else info.get("streaming", "false") == "true",
                "vram_gb": info.get("vram_gb", "—"),
                "hf_name": info["hf_name"],
                "inprocess_supported": bool(info.get("inprocess_supported", False)),
                # `downloaded` (None: not applicable or unknown) and `size` of the in-process copy.
                **self._download_state(model_id, resolved),
            }
            for model_id, info in MODEL_REGISTRY.items()
        ]

    def get_current_model(self) -> dict:
        model = self._config.inference.model
        flagged = bool(MODEL_REGISTRY.get(model, {}).get("diarization"))
        # Whether the engine that is running can do it: Parakeet's speaker detection is part of its Docker
        # container, so the in-process CPU engine cannot. Without a running app, assume the container.
        transcriber = self._get_transcriber() if self._get_transcriber else None
        engine_can = transcriber.supports_diarization if transcriber else True
        # `diarization`: the upload dialog's speaker detection works with this model; `diarization_models`
        # names the models it works with, and `diarization_hint` says why it is off for a model that has it.
        return {
            "model": model,
            "diarization": flagged and engine_can,
            # A model without the flag still gets speakers on an upload: the job runs on Parakeet instead.
            "diarization_fallback": not flagged,
            "diarization_models": speaker_detection_models(),
            "diarization_hint": transcriber.no_diarization_reason if flagged and not engine_can else "",
        }

    def set_model(self, model_id: str) -> dict:
        if model_id not in MODEL_REGISTRY:
            return {"ok": False, "error": f"Unknown model: {model_id}"}
        info = MODEL_REGISTRY[model_id]
        if self._on_model_switch:
            try:
                self._on_model_switch(model_id)
            except Exception as exc:
                logger.error("Model switch to %s could not start: %s", model_id, exc)
                return {"ok": False, "error": f"The model switch could not start: {exc}"}
            return {"ok": True, "status": "switching"}
        # No callback — update config directly (fallback for tests / no app)
        self._config.inference.model = model_id
        default_url = info.get("default_url")
        if default_url:
            self._config.inference.server_url = str(default_url)
        save_config(self._config)
        return {"ok": True, "model": model_id}

    # ---- Download of the speech model ---------------------------------------

    def _local_model(self) -> str | None:
        """The model this PC loads in-process and may have to download: the running engine's, or the config's."""
        transcriber = self._get_transcriber() if self._get_transcriber else None
        if transcriber is not None:
            return transcriber.local_model
        inference = self._config.inference
        info = MODEL_REGISTRY.get(inference.model, {})
        needs_docker = model_needs_docker(inference.model, resolve_device(inference.device))
        return None if info.get("custom") or needs_docker else inference.model

    def get_model_download(self) -> dict:
        """What the setup's model step and Settings > Models show before anything is downloaded.

        `hardware` is "gpu" (an NVIDIA GPU) or "cpu". `runs` is "local" (in this process), "docker" (the model's
        container) or "custom" (the user's server); `backup` is set for engine "auto" on a GPU, whose CPU copy
        transcribes while the container is down. `state` is "none" (nothing to download here), "missing", "downloading"
        (with `progress` in percent, None when unknown), "ready" or "error" (with `error`, and `offline` when there was
        no connection).
        """
        inference = self._config.inference
        model_id = inference.model
        info = MODEL_REGISTRY.get(model_id, {})
        resolved = resolve_device(inference.device)
        local = self._local_model()
        backup = bool(local) and model_needs_docker(model_id, resolved)
        if info.get("custom"):
            runs = "custom"
        else:
            runs = "local" if local and not backup else "docker"
        result: dict = {
            "hardware": "gpu" if cuda_available() else "cpu",
            "model": model_id,
            "name": str(info.get("display_name", model_id)),
            "size": str(info.get("download_size", "")),
            "runs": runs,
            "backup": backup,
            "state": "none",
            "progress": None,
            "error": None,
            "offline": False,
        }
        if not local:
            return result
        job = model_download.job(local)
        if job["state"] == "downloading":
            result.update(state="downloading", progress=model_download.progress(local))
        elif job["state"] == "error":
            # Before the probe: a download that broke off may have left the probe file, but not the rest.
            result.update(state="error", error=job["error"], offline=job["offline"])
        elif job["state"] == "ready" or model_download.is_downloaded(local) is not False:
            result["state"] = "ready"
            if job["state"] == "ready":
                self._download_cache.pop(local, None)  # the Settings list marks it downloaded at once
        else:
            result["state"] = "missing"
        return result

    def start_model_download(self) -> dict:
        """The user's click on Download (or Retry): the one way a speech model gets downloaded."""
        transcriber = self._get_transcriber() if self._get_transcriber else None
        local = transcriber.local_model if transcriber is not None else None
        if not local:
            return {**self.get_model_download(), "refused": "This model has nothing to download on this PC"}
        if self.get_model_download()["state"] in ("ready", "downloading"):
            return self.get_model_download()
        model_download.start(local, transcriber.download_model)
        return self.get_model_download()

    def get_model_status(self) -> dict:
        status = self._get_switch_status() if self._get_switch_status else {"status": "idle"}
        if status.get("status") == "ready":
            self._download_cache.clear()  # a model just finished loading, so it is on disk now
        return status

    # ---- Device ------------------------------------------------------------

    def get_device(self) -> dict:
        requested = self._config.inference.device
        return {
            "requested": requested,
            "resolved": resolve_device(requested),
            "cuda_available": cuda_available(),
            "active": self._active_device(),
        }

    def _active_device(self) -> str | None:
        """Where the running transcriber really works: "cpu" or "gpu", None when unknown.

        `resolved` is the device the config asks for. Auto-detect on a GPU PC without a running container, or a
        model with only a CPU path, still runs on the CPU; a custom server's hardware is not ours to name.
        """
        transcriber = self._get_transcriber() if self._get_transcriber else None
        device = getattr(transcriber, "device", None)
        if device in ("cpu", "gpu"):
            return device
        return "gpu" if device == "local" else None  # "local": the model's Docker container on this PC

    def set_device(self, requested: str) -> dict:
        if requested not in ("auto", "cuda", "cpu"):
            return {"ok": False, "error": "device must be 'auto', 'cuda', or 'cpu'"}
        self._config.inference.device = requested
        save_config(self._config)
        return {
            "ok": True,
            "requires_restart": True,
            **self.get_device(),
        }
