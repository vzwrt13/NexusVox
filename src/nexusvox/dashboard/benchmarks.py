"""Benchmark file I/O and comparison logic for the Dev tab."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# WER targets from the roadmap (fraction, not percentage).
WER_TARGETS: dict[str, float] = {
    "en_us": 0.08,
    "de_de": 0.12,
}


def list_benchmarks(benchmarks_dir: Path) -> list[dict]:
    """Return summary metadata for every benchmark JSON in *benchmarks_dir*."""
    results: list[dict] = []
    if not benchmarks_dir.is_dir():
        return results

    for path in sorted(benchmarks_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            s = data.get("summary", {})
            results.append(
                {
                    "filename": path.name,
                    "model_key": s.get("model_key", ""),
                    "model": s.get("model", ""),
                    "lang": s.get("lang", ""),
                    "split": s.get("split", ""),
                    "timestamp": s.get("timestamp", ""),
                    "total_samples": s.get("total_samples", 0),
                    "failed_samples": s.get("failed_samples", 0),
                    "total_audio_duration_s": s.get("total_audio_duration_s", 0),
                    "total_wall_time_s": s.get("total_wall_time_s", 0),
                    "rtf": s.get("rtf", 0),
                    "wer_mean": s.get("wer_mean", 0),
                    "wer_median": s.get("wer_median", 0),
                    "wer_p90": s.get("wer_p90", 0),
                    "wer_min": s.get("wer_min", 0),
                    "wer_max": s.get("wer_max", 0),
                    "wer_stddev": s.get("wer_stddev", 0),
                }
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping invalid benchmark file %s: %s", path.name, exc)

    return results


def load_benchmark(benchmarks_dir: Path, filename: str) -> dict | None:
    """Load full benchmark data (summary + samples) for a single file.

    Returns *None* if the file doesn't exist or is invalid.
    """
    path = (benchmarks_dir / filename).resolve()

    # Ensure the resolved path is still inside benchmarks_dir (path traversal guard).
    if not str(path).startswith(str(benchmarks_dir.resolve())):
        return None

    if not path.is_file():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load benchmark %s: %s", filename, exc)
        return None


def compare_benchmarks(benchmarks_dir: Path) -> dict:
    """Build a cross-model comparison structure from all available benchmarks.

    Returns::

        {
            "targets": {"en_us": 0.08, "de_de": 0.12},
            "benchmarks": [
                {
                    ...summary fields...,
                    "wer_target": 0.08,
                    "passes_target": True,
                },
                ...
            ],
        }
    """
    items = list_benchmarks(benchmarks_dir)
    for item in items:
        lang = item.get("lang", "")
        target = WER_TARGETS.get(lang)
        item["wer_target"] = target
        item["passes_target"] = item["wer_mean"] <= target if target is not None else None

    return {
        "targets": WER_TARGETS,
        "benchmarks": items,
    }
