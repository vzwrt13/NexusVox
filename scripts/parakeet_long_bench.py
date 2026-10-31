"""Measure Parakeet accuracy on long audio (over 20 s): raw WER and WER after the app's text pipeline.

Builds long test items by joining consecutive FLEURS clips of a language (0.6 s of silence between
them, the references joined the same way), so there are many long clips instead of the few FLEURS
has. Every item is decoded by the in-process CPU path, or by the GPU container with --gpu-url, and
scored twice against the reference:

  raw       the model output as is, normalised (lowercase, no punctuation) - comparable with the
            numbers in docs/parakeet-cpu-test.md
  pipeline  the output after `pipeline.process_text` with the settings of a real config.toml (numbers
            as digits, dictionary, snippets), normalised the same way: what the user actually sees.
            FLEURS references write numbers as digits, so this is the fairer figure.

Short clips (up to 20 s) are scored too, to show that dictation does not get worse.

Needs the clip folders and helpers of scripts/parakeet_cpu_bench.py (fetch the clips once with its
--fetch option). Heavy CPU load: run it while nobody works on the machine.

    python scripts/parakeet_long_bench.py fleurs --target-s 45 --items 8
    python scripts/parakeet_long_bench.py fleurs --config config.toml --gpu-url http://localhost:8002/v1/audio/transcriptions
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from parakeet_cpu_bench import (  # noqa: E402
    DEFAULT_LANGS,
    DIRECT_LIMIT_S,
    SR,
    CpuBackend,
    GpuBackend,
    edit_distance,
    normalize,
    read_wav,
    wer_of,
)

GAP = np.zeros(int(0.6 * SR), dtype=np.float32)


def join_clips(clips: list[tuple[np.ndarray, str]], target_s: float, items: int) -> list[tuple[np.ndarray, str]]:
    """Consecutive clips joined into items of at least `target_s` seconds; at most `items` of them."""
    out: list[tuple[np.ndarray, str]] = []
    parts: list[np.ndarray] = []
    refs: list[str] = []
    total = 0.0
    for samples, ref in clips:
        parts += [samples, GAP]
        refs.append(ref)
        total += (len(samples) + len(GAP)) / SR
        if total >= target_s:
            out.append((np.concatenate(parts), " ".join(refs)))
            parts, refs, total = [], [], 0.0
            if len(out) == items:
                break
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fleurs_dir", type=Path)
    ap.add_argument("--langs", nargs="+", default=DEFAULT_LANGS)
    ap.add_argument("--target-s", type=float, default=45.0, help="minimum length of a long item")
    ap.add_argument("--items", type=int, default=8, help="long items per language")
    ap.add_argument("--config", type=Path, default=Path("config.toml"), help="settings for the pipeline WER")
    ap.add_argument("--gpu-url", help="measure the GPU container at this endpoint instead of the CPU path")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    from nexusvox.config import load_config
    from nexusvox.pipeline import process_text

    config = load_config(args.config)
    backend = GpuBackend(args.gpu_url) if args.gpu_url else CpuBackend(None)
    if not args.gpu_url:
        backend.tr._get_model(backend.tr._onnx_model, backend.tr._quantization)  # load before timing starts

    def pipeline_text(raw: str) -> str:
        return process_text(raw, config, [], []).typed

    report: dict = {"backend": "gpu" if args.gpu_url else "cpu", "languages": {}}
    for lang in args.langs:
        refs_file = args.fleurs_dir / lang / "refs.tsv"
        if not refs_file.exists():
            continue
        rows = [ln.split("\t", 1) for ln in refs_file.read_text("utf-8").splitlines() if ln]
        clips = [(read_wav(args.fleurs_dir / lang / f), ref) for f, ref in rows]
        long_items = join_clips(clips, args.target_s, args.items)
        short_items = [c for c in clips if len(c[0]) <= DIRECT_LIMIT_S * SR]

        scores: dict[str, dict[str, list[tuple[int, int]]]] = {
            "short": {"raw": [], "pipeline": []},
            "long": {"raw": [], "pipeline": []},
        }
        for kind, items in (("short", short_items), ("long", long_items)):
            for samples, ref in items:
                text = backend.recognize(samples)
                ref_words = normalize(ref).split()
                for name, hyp in (("raw", text), ("pipeline", pipeline_text(text))):
                    scores[kind][name].append((edit_distance(ref_words, normalize(hyp).split()), len(ref_words)))
        report["languages"][lang] = {
            "short_clips": len(short_items),
            "long_items": len(long_items),
            "long_item_s": round(sum(len(x) for x, _ in long_items) / SR / max(len(long_items), 1), 1),
            "wer_short_raw": wer_of(scores["short"]["raw"]),
            "wer_short_pipeline": wer_of(scores["short"]["pipeline"]),
            "wer_long_raw": wer_of(scores["long"]["raw"]),
            "wer_long_pipeline": wer_of(scores["long"]["pipeline"]),
        }

    print(json.dumps(report, indent=2))
    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
