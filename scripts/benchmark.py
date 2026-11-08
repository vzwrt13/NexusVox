"""
Benchmark any openai_http transcription model on the FLEURS dataset.

Audio Dataset: google/fleurs

Quick usage:

  # Install deps (one-time)
  pip install jiwer requests tqdm soundfile librosa
  pip install "datasets<3.0.0"

  # 50-sample smoke test (whisper)
  python scripts/benchmark.py --model whisper-large-v3-turbo --lang en_us --limit 50

  # Parakeet, both languages, 200 samples each
  python scripts/benchmark.py --model parakeet-tdt-0.6b --lang both --limit 200

  # Parakeet, full test split, German only
  python scripts/benchmark.py --model parakeet-tdt-0.6b --lang de_de

  # A server you run yourself (e.g. Qwen3-ASR on llama-server), with a VRAM report
  python scripts/benchmark.py --model custom-server --url http://127.0.0.1:8008/v1/audio/transcriptions \
      --model-name qwen3-asr-1.7b --lang both

  # List available models
  python scripts/benchmark.py --list-models
"""

from __future__ import annotations

import argparse
import io
import json
import re
import statistics
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from itertools import islice
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

# Allow importing from src/ when running as a standalone script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from nexusvox.config import MODEL_REGISTRY  # noqa: E402
from nexusvox.transcription.transcriber import strip_asr_text_prefix  # noqa: E402

try:
    import numpy as np
except ImportError:
    sys.exit("numpy is required: pip install numpy")

try:
    from datasets import load_dataset
except ImportError:
    sys.exit("datasets is required: pip install datasets")

try:
    import requests
except ImportError:
    sys.exit("requests is required: pip install requests")

try:
    from jiwer import wer as compute_wer
except ImportError:
    sys.exit("jiwer is required: pip install jiwer")

try:
    from tqdm import tqdm

    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

HTTP_MODELS = {k: v for k, v in MODEL_REGISTRY.items() if v["protocol"] == "openai_http"}
INPROCESS_MODELS = {k: v for k, v in MODEL_REGISTRY.items() if v.get("inprocess_supported")}
BENCHMARKS_DIR = _PROJECT_ROOT / "benchmarks"
CUSTOM_MODEL = "custom-server"
VRAM_POLL_INTERVAL_S = 0.5


class VramMonitor:
    """Reads the GPU memory in use through nvidia-smi: once at the start, then the peak while a run is going.

    The reading covers the whole GPU, so other processes show up in it. Without nvidia-smi (or a GPU) every value
    stays ``None`` and the run goes on.
    """

    def __init__(self, interval_s: float = VRAM_POLL_INTERVAL_S) -> None:
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.start_mib: int | None = None
        self.peak_mib: int | None = None

    @staticmethod
    def read_mib() -> int | None:
        """Memory in use on the busiest GPU in MiB, or ``None`` when it cannot be read."""
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout
            return max(int(line) for line in out.split() if line.strip().isdigit())
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def start(self) -> None:
        self.start_mib = self.peak_mib = self.read_mib()
        if self.start_mib is None:
            return
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def _poll(self) -> None:
        while not self._stop.wait(self._interval_s):
            self.sample()

    def sample(self) -> None:
        value = self.read_mib()
        if value is not None and (self.peak_mib is None or value > self.peak_mib):
            self.peak_mib = value

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_s + 5)
            self.sample()

    def summary(self) -> dict:
        """Start value, peak, and the peak's increase over the start, all in MiB."""
        increase = None if self.start_mib is None or self.peak_mib is None else self.peak_mib - self.start_mib
        return {"vram_start_mib": self.start_mib, "vram_peak_mib": self.peak_mib, "vram_peak_increase_mib": increase}


def clean_hypothesis(text: str, model_key: str) -> str:
    """Remove the ``language <Name><asr_text>`` marker Qwen3-ASR behind llama-server puts before a transcript."""
    return strip_asr_text_prefix(text) if model_key == CUSTOM_MODEL else text


def list_models() -> None:
    """Print available models and exit."""
    print("Server-backed models (openai_http protocol, require GPU container):\n")
    for key, info in HTTP_MODELS.items():
        print(f"  {key:<30s} {info['display_name']}")
        print(f"    {info['description']}")
        if info["docker_profile"]:
            print(f"    Docker:      docker compose --profile {info['docker_profile']} up --build")
        print()
    print("\nIn-process models (--device cpu, no server needed):\n")
    for key, info in INPROCESS_MODELS.items():
        print(f"  {key:<30s} {info['display_name']}")
        print(f"    {info['description']}")
        print()
    print(f"\nYour own server (HTTP): {CUSTOM_MODEL}, with --url and --model-name\n")
    ws_models = [k for k, v in MODEL_REGISTRY.items() if v["protocol"] != "openai_http"]
    if ws_models:
        print(f"Not supported (WebSocket streaming): {', '.join(ws_models)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark a transcription model on the FLEURS dataset.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    all_models = sorted(set(HTTP_MODELS) | set(INPROCESS_MODELS) | {CUSTOM_MODEL})
    parser.add_argument("--model", choices=all_models, help="Model to benchmark")
    parser.add_argument("--list-models", action="store_true", help="List available models and exit")
    parser.add_argument("--lang", choices=["en_us", "de_de", "both"], default="en_us")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Max samples per language")
    parser.add_argument(
        "--url",
        default=None,
        help="Override transcription endpoint URL (required for --model custom-server)",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help="Name sent to the server and stored in the results (custom-server only)",
    )
    parser.add_argument("--split", default="test", help="HuggingFace dataset split")
    parser.add_argument("--timeout", type=float, default=120.0, metavar="SECONDS")
    parser.add_argument(
        "--device",
        choices=["gpu", "cpu"],
        default="gpu",
        help="gpu: POST to HTTP server; cpu: run LocalWhisperTranscriber in-process",
    )
    parser.add_argument(
        "--compute-type",
        default=None,
        help="CT2 compute type for --device cpu (int8, int8_float16, float16, float32). "
        "Default int8 on cpu, float16 on cuda.",
    )
    args = parser.parse_args()

    if args.list_models:
        list_models()
        sys.exit(0)

    if args.model is None:
        parser.error("--model is required (use --list-models to see options)")

    if args.model == CUSTOM_MODEL:
        if not args.url:
            parser.error("--model custom-server needs --url, e.g. http://127.0.0.1:8008/v1/audio/transcriptions")
        if args.device == "cpu":
            parser.error("--model custom-server is a server you run yourself; drop --device cpu")
        args.model_info = None
        args.model_hf_name = args.model_name or CUSTOM_MODEL
        args.langs = ["en_us", "de_de"] if args.lang == "both" else [args.lang]
        return args

    args.model_info = MODEL_REGISTRY[args.model]
    args.model_hf_name = args.model_info["hf_name"]

    if args.device == "cpu":
        if not args.model_info.get("inprocess_supported"):
            parser.error(
                f"--model {args.model!r} does not support in-process (--device cpu). "
                f"Pick one of: {', '.join(sorted(INPROCESS_MODELS))}"
            )
    else:
        if args.model not in HTTP_MODELS:
            parser.error(
                f"--model {args.model!r} has no HTTP server (protocol={args.model_info['protocol']}). "
                f"Use --device cpu for in-process, or pick: {', '.join(sorted(HTTP_MODELS))}"
            )
        if args.url is None:
            args.url = args.model_info["default_url"]

    args.langs = ["en_us", "de_de"] if args.lang == "both" else [args.lang]
    return args


def health_check(url: str, model_info: dict, timeout: float = 5.0) -> None:
    """Check that the inference server is reachable and the model is loaded."""
    health_url = model_info["health_url"]
    display = model_info["display_name"]
    profile = model_info["docker_profile"]
    try:
        with urlopen(health_url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            status = body.get("status", "")
            if status == "loading":
                sys.exit(f"{display} model is still loading. Try again in a moment. ({health_url})")
            if resp.status != 200 or status != "ok":
                sys.exit(f"{display} health check returned unexpected response: {body} ({health_url})")
    except URLError as exc:
        sys.exit(
            f"Cannot reach {display} server at {health_url}: {exc.reason}\n"
            f"Is the container running?  cd docker && docker compose --profile {profile} up"
        )


def custom_health_check(url: str, timeout: float = 5.0) -> None:
    """Probe ``/health`` on the custom server's host (llama-server has it); only an unreachable host aborts."""
    parts = urlsplit(url)
    health_url = f"{parts.scheme}://{parts.netloc}/health"
    try:
        with urlopen(health_url, timeout=timeout):
            pass
    except HTTPError:
        pass  # the host answered; the server may have no /health route, the first sample will tell
    except (URLError, OSError) as exc:
        sys.exit(f"Cannot reach the custom server at {health_url}: {getattr(exc, 'reason', exc)}\nIs it running?")


def float32_to_wav_bytes(audio_array: np.ndarray, sample_rate: int) -> bytes:
    """Convert a float32 audio array to a PCM16 WAV bytes object."""
    clipped = np.clip(audio_array, -1.0, 1.0)
    pcm16 = (clipped * 32767).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return buf.getvalue()


def transcribe(
    wav_bytes: bytes, url: str, model_hf_name: str, timeout: float, model_key: str = ""
) -> tuple[str | None, float | None, str | None]:
    """POST WAV to transcription endpoint, return (hypothesis, latency_s, error)."""
    t0 = time.monotonic()
    try:
        resp = requests.post(
            url,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"model": model_hf_name},
            timeout=timeout,
        )
        latency = time.monotonic() - t0
        if not resp.ok:
            return None, None, f"HTTP {resp.status_code}: {resp.text[:200]}"
        text = clean_hypothesis(resp.json().get("text", ""), model_key).strip()
        return text, latency, None
    except requests.exceptions.Timeout:
        return None, None, f"Timeout after {timeout}s"
    except requests.exceptions.ConnectionError as exc:
        return None, None, f"Connection error: {exc}"


def transcribe_inprocess(
    model, wav_bytes: bytes, language: str | None, timeout: float
) -> tuple[str | None, float | None, str | None]:
    """Run a preloaded faster-whisper model on WAV bytes. Returns (hypothesis, latency_s, error)."""
    t0 = time.monotonic()
    try:
        segments, _info = model.transcribe(
            io.BytesIO(wav_bytes),
            beam_size=1,
            language=language,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        latency = time.monotonic() - t0
        if latency > timeout:
            return None, None, f"Timeout after {timeout}s (actual {latency:.1f}s)"
        return text, latency, None
    except Exception as exc:
        return None, None, f"In-process error: {exc}"


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace for fair WER comparison."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)  # remove punctuation, keep letters/digits/whitespace
    return re.sub(r"\s+", " ", text.strip())


def _load_inprocess_model(args: argparse.Namespace):
    """Load and return a faster-whisper WhisperModel instance for --device cpu runs."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper is required for --device cpu: pip install faster-whisper")

    compute_type = args.compute_type or "int8"
    print(f"\nLoading {args.model_hf_name} on cpu ({compute_type}) — first run downloads the model …")
    t0 = time.monotonic()
    model = WhisperModel(args.model_hf_name, device="cpu", compute_type=compute_type)
    print(f"Model ready in {time.monotonic() - t0:.1f}s.")
    return model


def run_benchmark(lang: str, args: argparse.Namespace, inproc_model=None) -> tuple[list[dict], dict]:
    # The app never gives Whisper a language (it always auto-detects), so the benchmark does not either.
    lang_hint = None

    print(f"\nLoading google/fleurs ({lang}, split={args.split}, streaming) …")
    ds = load_dataset("google/fleurs", lang, split=args.split, streaming=True, trust_remote_code=True)
    samples_iter = islice(ds, args.limit)

    vram = VramMonitor()
    vram.start()
    results: list[dict] = []
    wer_values: list[float] = []
    total_audio_s = 0.0
    run_start = time.monotonic()

    counter = iter(range(args.limit if args.limit else 10**9))
    label = f"{lang} ({args.limit or '∞'} samples)"

    if HAS_TQDM:
        pbar = tqdm(total=args.limit, desc=label, unit="sample")

    for i, sample in enumerate(samples_iter):
        audio_array = sample["audio"]["array"]
        sample_rate = sample["audio"]["sampling_rate"]
        reference = sample.get("transcription") or sample.get("raw_transcription", "")
        sample_id = str(sample.get("id", f"sample_{i}"))
        audio_duration_s = len(audio_array) / sample_rate
        total_audio_s += audio_duration_s

        wav_bytes = float32_to_wav_bytes(audio_array, sample_rate)
        if args.device == "cpu":
            hypothesis, latency_s, error = transcribe_inprocess(inproc_model, wav_bytes, lang_hint, args.timeout)
        else:
            hypothesis, latency_s, error = transcribe(wav_bytes, args.url, args.model_hf_name, args.timeout, args.model)

        sample_wer: float | None = None
        if error is None:
            sample_wer = compute_wer(normalize(reference), normalize(hypothesis))
            wer_values.append(sample_wer)

        results.append(
            {
                "index": i,
                "id": sample_id,
                "reference": reference,
                "hypothesis": normalize(hypothesis) if hypothesis is not None else None,
                "hypothesis_raw": hypothesis,
                "wer": sample_wer,
                "audio_duration_s": round(audio_duration_s, 3),
                "latency_s": round(latency_s, 3) if latency_s is not None else None,
                "error": error,
            }
        )

        if HAS_TQDM:
            postfix = {"wer": f"{sample_wer:.3f}" if sample_wer is not None else "err"}
            if wer_values:
                postfix["mean"] = f"{statistics.mean(wer_values):.3f}"
            pbar.set_postfix(postfix)
            pbar.update(1)
        else:
            next(counter)
            status = f"wer={sample_wer:.3f}" if sample_wer is not None else f"error={error}"
            print(f"  [{i + 1}] {status}")

    if HAS_TQDM:
        pbar.close()

    wall_time = time.monotonic() - run_start
    vram.stop()
    n_ok = len(wer_values)
    # RTF without the dataset streaming time: only the transcription requests count.
    ok = [r for r in results if r["latency_s"] is not None]
    latencies = [r["latency_s"] for r in ok]
    audio_ok_s = sum(r["audio_duration_s"] for r in ok)

    summary: dict = {
        "model": args.model_hf_name,
        "model_key": args.model,
        "dataset": "google/fleurs",
        "lang": lang,
        "split": args.split,
        "limit": args.limit,
        "url": args.url if args.device == "gpu" else None,
        "device": args.device,
        "compute_type": args.compute_type if args.device == "cpu" else None,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "total_samples": len(results),
        "failed_samples": len(results) - n_ok,
        "total_audio_duration_s": round(total_audio_s, 2),
        "total_wall_time_s": round(wall_time, 2),
        "rtf": round(wall_time / total_audio_s, 4) if total_audio_s > 0 else None,
        "rtf_server": round(sum(latencies) / audio_ok_s, 4) if audio_ok_s > 0 else None,
        **vram.summary(),
    }

    if n_ok >= 2:
        sorted_wers = sorted(wer_values)
        summary.update(
            {
                "wer_mean": round(statistics.mean(wer_values), 6),
                "wer_median": round(statistics.median(wer_values), 6),
                "wer_p90": round(statistics.quantiles(sorted_wers, n=10)[8], 6),
                "wer_min": round(sorted_wers[0], 6),
                "wer_max": round(sorted_wers[-1], 6),
                "wer_stddev": round(statistics.stdev(wer_values), 6),
            }
        )
    elif n_ok == 1:
        summary.update(
            {
                "wer_mean": round(wer_values[0], 6),
                "wer_median": round(wer_values[0], 6),
                "wer_p90": round(wer_values[0], 6),
                "wer_min": round(wer_values[0], 6),
                "wer_max": round(wer_values[0], 6),
                "wer_stddev": None,
            }
        )
    else:
        summary.update(
            {
                "wer_mean": None,
                "wer_median": None,
                "wer_p90": None,
                "wer_min": None,
                "wer_max": None,
                "wer_stddev": None,
            }
        )

    return results, summary


def save_results(model_key: str, lang: str, results: list[dict], summary: dict) -> Path:
    BENCHMARKS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = BENCHMARKS_DIR / f"{model_key}_{lang}_{ts}.json"
    payload = {"summary": summary, "samples": results}
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return out_path


def main() -> None:
    args = parse_args()
    inproc_model = None
    if args.device == "cpu":
        inproc_model = _load_inprocess_model(args)
    else:
        if args.model == CUSTOM_MODEL:
            custom_health_check(args.url)
        else:
            health_check(args.url, args.model_info)

    for lang in args.langs:
        results, summary = run_benchmark(lang, args, inproc_model=inproc_model)
        out_path = save_results(args.model, lang, results, summary)
        wer_str = f"{summary['wer_mean']:.4f}" if summary["wer_mean"] is not None else "n/a"
        rtf_str = f"{summary['rtf']:.3f}" if summary["rtf"] is not None else "n/a"
        server_rtf_str = f"{summary['rtf_server']:.3f}" if summary["rtf_server"] is not None else "n/a"
        print(
            f"\n{lang}: WER={wer_str}  RTF={rtf_str}  server-RTF={server_rtf_str}  "
            f"samples={summary['total_samples']}  failed={summary['failed_samples']}"
        )
        if summary["vram_peak_mib"] is not None:
            print(
                f"VRAM: start {summary['vram_start_mib']} MiB, peak {summary['vram_peak_mib']} MiB "
                f"(+{summary['vram_peak_increase_mib']} MiB over the start)"
            )
        print(f"Results: {out_path}")

    if len(args.langs) > 1:
        print("\nAll languages complete.")


if __name__ == "__main__":
    main()
