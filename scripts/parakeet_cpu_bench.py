"""Measure the in-process CPU Parakeet path (onnx-asr, int8) on FLEURS clips.

Reports model load time, the first (cold) transcription, dictation latency for short and normal
clips, real-time factor for a long concatenated file, peak RAM and word error rate per language.
Clips are fetched separately (see docs/parakeet-cpu-test.md); this script only reads them.

    python scripts/parakeet_cpu_bench.py <fleurs-dir> [--long-minutes 10] [--out result.json]

<fleurs-dir>/<lang>/ holds the WAV files plus refs.tsv ("<file>\t<reference text>" per line).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import re
import statistics
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SR = 16_000


class _Counters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def mem_mb() -> tuple[float, float]:
    """(current, peak) working set of this process in MB."""
    c = _Counters()
    c.cb = ctypes.sizeof(c)
    kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize / 2**20, c.PeakWorkingSetSize / 2**20


def normalize(text: str) -> str:
    text = re.sub(r"[^\w\s]", "", text.lower())
    return re.sub(r"\s+", " ", text.strip())


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def read_wav(path: Path) -> np.ndarray:
    """Mono 16 kHz WAV as float32; handles PCM16 and the float32 files FLEURS ships (stdlib `wave` cannot)."""
    raw = path.read_bytes()
    pos, fmt, samples = 12, None, None
    while pos + 8 <= len(raw):
        cid, size = raw[pos : pos + 4], int.from_bytes(raw[pos + 4 : pos + 8], "little")
        body = raw[pos + 8 : pos + 8 + size]
        if cid == b"fmt ":
            fmt = (
                int.from_bytes(body[0:2], "little"),
                int.from_bytes(body[2:4], "little"),
                int.from_bytes(body[4:8], "little"),
                int.from_bytes(body[14:16], "little"),
            )
        elif cid == b"data":
            samples = body
        pos += 8 + size + (size & 1)
    tag, channels, rate, bits = fmt
    assert channels == 1 and rate == SR, (path, fmt)
    if tag == 3 and bits == 32:
        return np.frombuffer(samples, dtype="<f4").astype(np.float32)
    assert tag == 1 and bits == 16, (path, fmt)
    return np.frombuffer(samples, dtype="<i2").astype(np.float32) / 32768.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fleurs_dir", type=Path)
    ap.add_argument("--long-minutes", type=float, default=10.0)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--show-errors", action="store_true", help="print reference and hypothesis of bad clips")
    args = ap.parse_args()

    out: dict = {}
    t0 = time.perf_counter()
    from nexusvox.config import InferenceConfig
    from nexusvox.transcribers.local_parakeet import LocalParakeetTranscriber

    out["import_s"] = round(time.perf_counter() - t0, 2)
    out["ram_after_import_mb"] = round(mem_mb()[0])

    tr = LocalParakeetTranscriber(InferenceConfig(model="parakeet-tdt-0.6b"))
    never = threading.Event()

    data: dict[str, list[tuple[np.ndarray, str]]] = {}
    for d in sorted(p for p in args.fleurs_dir.iterdir() if p.is_dir()):
        rows = [ln.split("\t", 1) for ln in (d / "refs.tsv").read_text(encoding="utf-8").splitlines() if ln]
        data[d.name] = [(read_wav(d / f), ref) for f, ref in rows]

    t = time.perf_counter()
    tr._get_model(tr._onnx_model, tr._quantization)
    out["model_load_s"] = round(time.perf_counter() - t, 2)
    out["ram_after_load_mb"] = round(mem_mb()[0])

    # First transcription after the load: the cold-start cost a user sees on the first dictation.
    first = data["en_us"][0][0]
    lat = []
    for _ in range(4):
        t = time.perf_counter()
        tr._recognize(first, never)
        lat.append(round(time.perf_counter() - t, 3))
    out["first_clip_s"] = round(len(first) / SR, 1)
    out["first_then_repeats_latency_s"] = lat

    # Dictation latency: 3 s and 8 s excerpts, and whole clips.
    base = data["en_us"][1][0]
    for secs in (3, 8):
        clip = base[: secs * SR]
        ls = []
        for _ in range(8):
            t = time.perf_counter()
            tr._recognize(clip, never)
            ls.append(time.perf_counter() - t)
        out[f"latency_{secs}s_median_s"] = round(statistics.median(ls), 3)
        out[f"latency_{secs}s_max_s"] = round(max(ls), 3)

    # Per language: WER, latency and RTF over whole clips.
    langs = {}
    for lang, items in data.items():
        errs = words = 0
        wers, dur, wall, per = [], 0.0, 0.0, []
        for samples, ref in items:
            t = time.perf_counter()
            text, _ = tr._recognize(samples, never)
            dt = time.perf_counter() - t
            r, h = normalize(ref).split(), normalize(text).split()
            e = edit_distance(r, h)
            errs, words = errs + e, words + len(r)
            wers.append(e / max(len(r), 1))
            dur, wall = dur + len(samples) / SR, wall + dt
            per.append(dt)
            if args.show_errors and e / max(len(r), 1) > 0.2:
                print(f"[{lang}] REF: {normalize(ref)}")
                print(f"[{lang}] HYP: {normalize(text)}")
        langs[lang] = {
            "clips": len(items),
            "audio_s": round(dur, 1),
            "wer_corpus": round(errs / words, 4),
            "wer_mean_per_clip": round(statistics.mean(wers), 4),
            "wer_median_per_clip": round(statistics.median(wers), 4),
            "rtf": round(wall / dur, 4),
            "latency_median_s": round(statistics.median(per), 3),
        }
    out["languages"] = langs

    # Long file: German and English clips cycled to the requested length (VAD-segmented path).
    pool = [s for lang in ("de_de", "en_us") for s, _ in data[lang]]
    parts, total, i = [], 0.0, 0
    gap = np.zeros(SR // 2, dtype=np.float32)
    while total < args.long_minutes * 60:
        parts += [pool[i % len(pool)], gap]
        total += (len(pool[i % len(pool)]) + len(gap)) / SR
        i += 1
    long_audio = np.concatenate(parts)
    t = time.perf_counter()
    text, _ = tr._recognize(long_audio, never)
    wall = time.perf_counter() - t
    out["long"] = {
        "audio_min": round(len(long_audio) / SR / 60, 1),
        "wall_s": round(wall, 1),
        "rtf": round(wall / (len(long_audio) / SR), 4),
        "words": len(text.split()),
    }
    cur, peak = mem_mb()
    out["ram_end_mb"], out["ram_peak_mb"] = round(cur), round(peak)

    print(json.dumps(out, indent=2))
    if args.out:
        args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
