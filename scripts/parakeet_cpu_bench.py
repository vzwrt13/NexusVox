"""Measure Parakeet speed and accuracy on FLEURS clips: in-process on CPU, or the GPU container.

CPU (the in-process onnx-asr int8 path the app uses) reports model load time, the first (cold)
transcription, dictation latency for 3 s and 8 s clips, per-language WER / RTF / latency (split at
the 20 s direct-decode limit), a long concatenated file and RAM. With --gpu-url the same clips go to
the Docker container's HTTP endpoint instead (cold start, latency, WER, RTF only).

    # fetch 25 clips per language into ./fleurs (once), then measure on 16 threads
    python scripts/parakeet_cpu_bench.py fleurs --fetch 25 --long-minutes 30
    # fewer logical processors (first N), to find the minimum hardware
    python scripts/parakeet_cpu_bench.py fleurs --threads 4 --long-minutes 5
    # GPU container (docker compose --profile parakeet up), same clips
    python scripts/parakeet_cpu_bench.py fleurs --gpu-url http://localhost:8002/v1/audio/transcriptions

Folders are named after the FLEURS language codes (de_de, en_us, es_419, fr_fr, pt_br); each holds the
WAV files and a refs.tsv ("<file>\\t<reference>" per line), which --fetch writes. Only languages whose
folder exists are measured. RAM figures need Windows (elsewhere the peak is the process maximum).
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import io
import json
import re
import statistics
import struct
import sys
import tarfile
import threading
import time
import urllib.request
import uuid
from ctypes import wintypes
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SR = 16_000
DIRECT_LIMIT_S = 20.0  # local_parakeet.MAX_DIRECT_S; clips above it are cut at pauses
FLEURS_URL = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/%s/"
DEFAULT_LANGS = ["de_de", "en_us", "es_419", "fr_fr", "pt_br"]
IS_WINDOWS = sys.platform == "win32"


# ---- process helpers -------------------------------------------------------------------------


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
    """(current, peak) working set of this process in MB; (0, 0) when it cannot be read."""
    if not IS_WINDOWS:
        try:
            import resource

            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB on Linux
            return peak, peak
        except Exception:
            return 0.0, 0.0
    c = _Counters()
    c.cb = ctypes.sizeof(c)
    kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb):
        return 0.0, 0.0
    return c.WorkingSetSize / 2**20, c.PeakWorkingSetSize / 2**20


def limit_threads(n: int) -> None:
    """Pin this process to the first `n` logical processors (call before onnxruntime starts its pool)."""
    if IS_WINDOWS:
        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
        if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), (1 << n) - 1):
            sys.exit(f"Could not limit the process to {n} logical processors")
    else:
        import os

        os.sched_setaffinity(0, set(range(n)))


# ---- text and audio --------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Same normaliser as scripts/benchmark_parakeet.py (keep the two in sync: WERs must stay comparable)."""
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


def wav_bytes(x: np.ndarray) -> bytes:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    hdr = (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16)
    )
    return hdr + b"data" + struct.pack("<I", len(pcm)) + pcm


# ---- FLEURS download -------------------------------------------------------------------------


def fetch_fleurs(root: Path, langs: list[str], count: int) -> None:
    """Stream each language's test tarball and keep the first `count` clips plus their references.

    The tarball is read as a stream and closed after `count` matching clips, so only a few MB per
    language are transferred instead of the 300-650 MB archive.
    """
    for lang in langs:
        d = root / lang
        if (d / "refs.tsv").exists():
            continue
        d.mkdir(parents=True, exist_ok=True)
        tsv = urllib.request.urlopen(FLEURS_URL % lang + "test.tsv").read().decode()
        # Columns: id, file name, raw transcription, normalised transcription, ...
        ref = {r[1]: r[3] for r in csv.reader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)}
        resp = urllib.request.urlopen(FLEURS_URL % lang + "audio/test.tar.gz")
        got: list[str] = []
        with tarfile.open(fileobj=resp, mode="r|gz") as tf:
            for m in tf:
                name = Path(m.name).name
                if m.isfile() and name in ref:
                    (d / name).write_bytes(tf.extractfile(m).read())
                    got.append(name)
                    if len(got) >= count:
                        break
        resp.close()
        (d / "refs.tsv").write_text("\n".join(f"{n}\t{ref[n]}" for n in got), encoding="utf-8")
        print(f"fetched {lang}: {len(got)} clips")


# ---- backends --------------------------------------------------------------------------------


class GpuBackend:
    """The Docker container's OpenAI-style HTTP endpoint."""

    def __init__(self, url: str) -> None:
        self.url = url

    def recognize(self, x: np.ndarray) -> str:
        crlf, bd = "\r\n", uuid.uuid4().hex
        head = (
            f'--{bd}{crlf}Content-Disposition: form-data; name="model"{crlf}{crlf}nvidia/parakeet-tdt-0.6b-v3{crlf}'
            f'--{bd}{crlf}Content-Disposition: form-data; name="file"; filename="a.wav"{crlf}'
            f"Content-Type: audio/wav{crlf}{crlf}"
        ).encode()
        body = head + wav_bytes(x) + f"{crlf}--{bd}--{crlf}".encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": f"multipart/form-data; boundary={bd}"})
        return json.load(urllib.request.urlopen(req, timeout=900))["text"]


class CpuBackend:
    """LocalParakeetTranscriber._recognize, the call the app makes."""

    def __init__(self, max_direct_s: float | None) -> None:
        from nexusvox.config import InferenceConfig
        from nexusvox.transcribers import local_parakeet

        if max_direct_s is not None:
            local_parakeet.MAX_DIRECT_S = max_direct_s
        self.tr = local_parakeet.LocalParakeetTranscriber(InferenceConfig(model="parakeet-tdt-0.6b"))
        self.never = threading.Event()

    def load(self) -> float:
        """Model load seconds; loaded up front so no clip's timing includes it."""
        t = time.perf_counter()
        self.tr._get_model(self.tr._onnx_model, self.tr._quantization)
        return time.perf_counter() - t

    def recognize(self, x: np.ndarray) -> str:
        return self.tr._recognize(x, self.never)[0]


def timed(backend, x: np.ndarray) -> tuple[str, float]:
    t = time.perf_counter()
    text = backend.recognize(x)
    return text, time.perf_counter() - t


# ---- main ------------------------------------------------------------------------------------


def wer_of(items: list[tuple[int, int]]) -> float | None:
    words = sum(w for _, w in items)
    return round(sum(e for e, _ in items) / words, 4) if words else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fleurs_dir", type=Path)
    ap.add_argument("--fetch", type=int, metavar="N", help="download the first N test clips per language if missing")
    ap.add_argument("--langs", nargs="+", default=DEFAULT_LANGS)
    ap.add_argument("--long-minutes", type=float, default=10.0, help="length of the long file (CPU only; 0 skips it)")
    ap.add_argument("--threads", type=int, help="pin to the first N logical processors")
    ap.add_argument("--max-direct-s", type=float, help="override MAX_DIRECT_S, the longest clip decoded in one call")
    ap.add_argument("--gpu-url", help="measure the GPU container at this endpoint instead of the CPU path")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--show-errors", action="store_true", help="print reference and hypothesis of bad clips")
    args = ap.parse_args()

    if args.fetch:
        args.fleurs_dir.mkdir(parents=True, exist_ok=True)
        fetch_fleurs(args.fleurs_dir, args.langs, args.fetch)
    langs = [lang for lang in args.langs if (args.fleurs_dir / lang / "refs.tsv").exists()]
    if not langs:
        sys.exit(f"No clips under {args.fleurs_dir}; run with --fetch 25 first")

    out: dict = {"backend": "gpu" if args.gpu_url else "cpu"}
    if args.threads:
        limit_threads(args.threads)
        out["threads"] = args.threads
    gpu = bool(args.gpu_url)
    backend = GpuBackend(args.gpu_url) if gpu else CpuBackend(args.max_direct_s)
    if args.max_direct_s:
        out["max_direct_s"] = args.max_direct_s

    # Model first, clips after: the "model" RAM figure then holds no benchmark data.
    if not gpu:
        out["model_load_s"] = round(backend.load(), 2)
        out["ram_model_mb"] = round(mem_mb()[0])

    data: dict[str, list[tuple[np.ndarray, str]]] = {}
    for lang in langs:
        rows = [ln.split("\t", 1) for ln in (args.fleurs_dir / lang / "refs.tsv").read_text("utf-8").splitlines() if ln]
        data[lang] = [(read_wav(args.fleurs_dir / lang / f), ref) for f, ref in rows]
    pool = [x for items in data.values() for x, _ in items]
    out["clip_data_mb"] = round(sum(x.nbytes for x in pool) / 2**20)

    # Cold start: the first request after the load (CPU) or after the container came up (GPU).
    first = data[langs[0]][0][0]
    out["first_clip_s"] = round(len(first) / SR, 1)
    out["first_then_repeats_latency_s"] = [round(timed(backend, first)[1], 3) for _ in range(4)]

    # Dictation latency on real speech cut to exactly 3 s and 8 s.
    for secs in (3, 8):
        base = next((x for x in pool if len(x) >= secs * SR), None)
        if base is None:
            sys.exit(f"No clip of at least {secs} s to cut a {secs} s dictation from")
        ls = [timed(backend, base[: secs * SR])[1] for _ in range(8)]
        out[f"latency_{secs}s_median_s"] = round(statistics.median(ls), 3)
        out[f"latency_{secs}s_max_s"] = round(max(ls), 3)

    # Per language: WER (all clips, and split at the direct-decode limit), latency and RTF.
    result = {}
    for lang, items in data.items():
        short: list[tuple[int, int]] = []
        long_: list[tuple[int, int]] = []
        dur = wall = 0.0
        per = []
        for samples, ref in items:
            text, dt = timed(backend, samples)
            r, h = normalize(ref).split(), normalize(text).split()
            e = edit_distance(r, h)
            (long_ if len(samples) > DIRECT_LIMIT_S * SR else short).append((e, len(r)))
            dur, wall = dur + len(samples) / SR, wall + dt
            per.append(dt)
            if args.show_errors and e / max(len(r), 1) > 0.2:
                print(f"[{lang}] REF: {normalize(ref)}")
                print(f"[{lang}] HYP: {normalize(text)}")
        result[lang] = {
            "clips": len(items),
            "audio_s": round(dur, 1),
            "wer_corpus": wer_of(short + long_),
            "wer_clips_up_to_20s": wer_of(short),
            "clips_up_to_20s": len(short),
            "wer_clips_over_20s": wer_of(long_),
            "clips_over_20s": len(long_),
            "rtf": round(wall / dur, 4),
            "latency_median_s": round(statistics.median(per), 3),
        }
    out["languages"] = result

    # Long file: de_de/en_us clips (or whatever is there) cycled to the requested length.
    if not gpu and args.long_minutes > 0:
        src = [x for lang in ("de_de", "en_us") if lang in data for x, _ in data[lang]] or pool
        gap = np.zeros(SR // 2, dtype=np.float32)
        parts, total, i = [], 0.0, 0
        while total < args.long_minutes * 60:
            parts += [src[i % len(src)], gap]
            total += (len(src[i % len(src)]) + len(gap)) / SR
            i += 1
        long_audio = np.concatenate(parts)
        del parts
        text, wall = timed(backend, long_audio)
        out["long"] = {
            "audio_min": round(len(long_audio) / SR / 60, 1),
            "wall_s": round(wall, 1),
            "rtf": round(wall / (len(long_audio) / SR), 4),
            "words": len(text.split()),
            "audio_buffer_mb": round(long_audio.nbytes / 2**20),
        }
    if not gpu:
        out["ram_end_mb"], out["ram_peak_mb"] = (round(v) for v in mem_mb())

    print(json.dumps(out, indent=2))
    if args.out:
        args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
