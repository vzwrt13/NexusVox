"""Dashboard API exposed to JavaScript via the pywebview JS bridge."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from ..config import MODEL_REGISTRY, Config, resolve_device, save_config
from ..db import Database
from ..file_transcribe import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, convert_to_wav, transcribe_file
from ..os_commands import NEXUS_ACTIONS
from ..voice_commands import ALL_SYMBOL_INFO
from . import analytics
from . import benchmarks as bench

logger = logging.getLogger(__name__)


class DashboardAPI:
    """Methods callable from JavaScript as ``pywebview.api.<method>(...)``."""

    def __init__(
        self,
        session_factory: sessionmaker,
        config: Config,
        db: Database | None = None,
        on_model_switch: Callable[[str], None] | None = None,
        get_switch_status: Callable[[], dict] | None = None,
        benchmarks_dir: Path | None = None,
    ) -> None:
        self._sf = session_factory
        self._config = config
        self._db = db
        self._on_model_switch = on_model_switch
        self._get_switch_status = get_switch_status
        self._benchmarks_dir = benchmarks_dir or Path("benchmarks")

    # ---- Models -------------------------------------------------------------

    def get_available_models(self) -> list[dict]:
        resolved = resolve_device(self._config.inference.device)
        return [
            {
                "id": model_id,
                "name": info["display_name"],
                "protocol": info["protocol"],
                "description": info.get("description", ""),
                "parameters": info.get("parameters", "—"),
                "architecture": info.get("architecture", "—"),
                "languages": info.get("languages", "—"),
                "streaming": info.get("streaming", "false") == "true",
                "vram_gb": info.get("vram_gb", "—"),
                "hf_name": info["hf_name"],
                "requires_gpu": bool(info.get("requires_gpu", True)),
                "inprocess_supported": bool(info.get("inprocess_supported", False)),
            }
            for model_id, info in MODEL_REGISTRY.items()
            if not (resolved == "cpu" and info.get("requires_gpu"))
        ]

    def get_current_model(self) -> dict:
        return {"model": self._config.inference.model}

    def set_model(self, model_id: str) -> dict:
        if model_id not in MODEL_REGISTRY:
            return {"ok": False, "error": f"Unknown model: {model_id}"}
        info = MODEL_REGISTRY[model_id]
        resolved = resolve_device(self._config.inference.device)
        if resolved == "cpu" and info.get("requires_gpu"):
            return {
                "ok": False,
                "error": f"Model {model_id} requires a GPU and is not available on CPU.",
            }
        if self._on_model_switch:
            self._on_model_switch(model_id)
            return {"ok": True, "status": "switching"}
        # No callback — update config directly (fallback for tests / no app)
        self._config.inference.model = model_id
        default_url = info.get("default_url")
        if default_url:
            self._config.inference.server_url = str(default_url)
        save_config(self._config)
        return {"ok": True, "model": model_id}

    def get_model_status(self) -> dict:
        if self._get_switch_status:
            return self._get_switch_status()
        return {"status": "idle"}

    # ---- Device ------------------------------------------------------------

    def get_device(self) -> dict:
        requested = self._config.inference.device
        return {
            "requested": requested,
            "resolved": resolve_device(requested),
            "cuda_available": resolve_device("auto") == "cuda",
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

    # ---- Settings ----------------------------------------------------------

    def get_settings(self) -> dict:
        return {
            "auto_language_detection": self._config.auto_language_detection,
            "language": self._config.language,
        }

    def set_auto_language_detection(self, enabled: bool) -> dict:
        self._config.auto_language_detection = enabled
        save_config(self._config)
        return self.get_settings()

    # ---- Voice Commands ----------------------------------------------------

    def get_voice_commands(self) -> dict:
        return {
            "enabled": self._config.voice_commands.enabled,
            "numbers_as_digits": self._config.voice_commands.numbers_as_digits,
            "bypass_symbols": self._config.voice_commands.bypass_symbols,
            "symbols": self._config.voice_commands.symbols,
            "all_symbols": ALL_SYMBOL_INFO,
        }

    def set_voice_commands_enabled(self, enabled: bool) -> dict:
        self._config.voice_commands.enabled = enabled
        save_config(self._config)
        return self.get_voice_commands()

    def set_voice_commands_numbers(self, enabled: bool) -> dict:
        self._config.voice_commands.numbers_as_digits = enabled
        save_config(self._config)
        return self.get_voice_commands()

    def set_voice_commands_symbols(self, symbols: list[str]) -> dict:
        self._config.voice_commands.symbols = symbols
        save_config(self._config)
        return self.get_voice_commands()

    def set_voice_commands_bypass_symbols(self, enabled: bool) -> dict:
        self._config.voice_commands.bypass_symbols = enabled
        save_config(self._config)
        return self.get_voice_commands()

    # ---- OS Commands -------------------------------------------------------

    def get_os_commands(self) -> dict:
        return {
            "enabled": self._config.os_commands.enabled,
            "apps": self._config.os_commands.apps,
            "supported_actions": NEXUS_ACTIONS,
        }

    def set_os_commands_enabled(self, enabled: bool) -> dict:
        self._config.os_commands.enabled = enabled
        save_config(self._config)
        return self.get_os_commands()

    def set_os_commands_apps(self, apps: dict[str, str]) -> dict:
        self._config.os_commands.apps = apps
        save_config(self._config)
        return self.get_os_commands()

    # ---- Analytics ---------------------------------------------------------

    def get_overview(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_overview(self._sf, start=start, end=end)

    def get_transcriptions_over_time(
        self, period: str = "day", start: str | None = None, end: str | None = None
    ) -> dict:
        return analytics.get_transcriptions_over_time(self._sf, period, start=start, end=end)

    def get_language_distribution(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_language_distribution(self._sf, start=start, end=end)

    def get_top_words(self, n: int = 20, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_top_words(self._sf, n, start=start, end=end)

    def get_peak_usage_hours(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_peak_usage_hours(self._sf, start=start, end=end)

    def get_activity_heatmap(self, start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_activity_heatmap(self._sf, start=start, end=end)

    # ---- Flagged / Corrections ---------------------------------------------

    def get_flagged_transcriptions(self) -> list[dict]:
        return analytics.get_flagged_transcriptions(self._sf)

    def get_audio_file_path(self, tid: int) -> str | None:
        """Resolve the absolute path to a transcription's audio file."""
        if self._db is None:
            return None
        from pathlib import Path

        from ..models import Transcription

        with self._sf() as session:
            row = session.get(Transcription, tid)
            if row and row.audio_path:
                db_dir = Path(self._config.database.path).resolve().parent
                return str(db_dir / row.audio_path)
        return None

    def update_correction(self, tid: int, corrected_text: str) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        ok = self._db.update_correction(tid, corrected_text)
        return {"ok": ok}

    # ---- Review ------------------------------------------------------------

    def get_unreviewed_transcriptions(self) -> list[dict]:
        return analytics.get_unreviewed_transcriptions(self._sf)

    def submit_review(self, tid: int, is_correct: bool, corrected_text: str | None = None) -> dict:
        if self._db is None:
            return {"ok": False, "error": "DB not available"}
        ok = self._db.submit_review(tid, is_correct, corrected_text)
        return {"ok": ok}

    def get_confidence_trend(self, period: str = "day", start: str | None = None, end: str | None = None) -> dict:
        return analytics.get_confidence_over_time(self._sf, period, start=start, end=end)

    # ---- File Upload Transcription -----------------------------------------

    def upload_and_transcribe(self, file_bytes: bytes, filename: str) -> dict:
        """Convert an uploaded audio file, transcribe it, and save to DB."""
        if self._db is None:
            return {"ok": False, "error": "DB not available"}

        from ..file_transcribe import _get_extension

        ext = _get_extension(filename)
        if ext not in ALLOWED_EXTENSIONS:
            return {"ok": False, "error": f"Unsupported format: {ext}"}
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            return {"ok": False, "error": f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"}

        try:
            wav_chunks, duration_ms = convert_to_wav(file_bytes, filename)
        except Exception as exc:
            logger.error("Audio conversion failed for %s: %s", filename, exc)
            return {"ok": False, "error": f"Audio conversion failed: {exc}"}

        try:
            result = asyncio.run(
                transcribe_file(
                    wav_chunks,
                    self._config.inference,
                    language=self._config.language,
                    auto_detect_language=self._config.auto_language_detection,
                )
            )
        except Exception as exc:
            logger.error("Transcription failed for %s: %s", filename, exc)
            return {"ok": False, "error": f"Transcription failed: {exc}"}

        record = self._db.save_file_transcription(
            text=result.text,
            language=self._config.language,
            duration_ms=duration_ms,
            original_filename=filename,
            model=self._config.inference.model,
            confidence=result.confidence,
        )

        return {
            "ok": True,
            "id": record.id,
            "text": record.text,
            "language": record.language,
            "duration_ms": record.duration_ms,
            "model": record.model,
            "original_filename": record.original_filename,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }

    def get_file_transcriptions(self, limit: int = 50) -> list[dict]:
        """Return file transcription records."""
        if self._db is None:
            return []
        rows = self._db.get_file_transcriptions(limit)
        return [
            {
                "id": r.id,
                "text": r.text,
                "language": r.language,
                "duration_ms": r.duration_ms,
                "model": r.model,
                "original_filename": r.original_filename,
                "confidence": r.confidence,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    # ---- Benchmarks (Dev tab) ----------------------------------------------

    def get_benchmarks(self) -> list[dict]:
        """List all benchmark files with summary stats."""
        return bench.list_benchmarks(self._benchmarks_dir)

    def get_benchmark(self, filename: str) -> dict | None:
        """Load full benchmark data for a single file."""
        return bench.load_benchmark(self._benchmarks_dir, filename)

    def get_benchmark_comparison(self) -> dict:
        """Cross-model comparison with pass/fail against WER targets."""
        return bench.compare_benchmarks(self._benchmarks_dir)
