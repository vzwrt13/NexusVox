"""Dashboard API: transcription models and the compute device."""

from __future__ import annotations

import logging
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
from ..transcription.diarization import speaker_detection_models
from .api_base import DashboardState

logger = logging.getLogger(__name__)

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
        }

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
