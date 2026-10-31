"""Measure a local Ollama model on the NexusVox language-model jobs: load time, Auto Cleanup and Polish
latency for a short and a long dictation, a meeting summary, memory use, and reply quality.

    python scripts/llm_bench.py --model gemma4:e2b --mode gpu --out results/gemma4-e2b-gpu.json
    python scripts/llm_bench.py --model gemma4:e2b --mode cpu --meeting meeting_de.txt --skip-quality

`--mode cpu` sends `num_gpu = 0`, so Ollama keeps every layer on the CPU. The prompts are the app's own
(`cleanup.PROMPTS`, `polish.SYSTEM_PROMPT`) and the reply checks are the ones `cleanup_eval.py` uses. The
meeting summary prompt is the benchmark's own: NexusVox has no summary feature yet. Needs a running Ollama;
the model must already be pulled. Nothing is typed or saved.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cleanup_eval import SAMPLES  # noqa: E402

from nexusvox.cleanup import _TAG_RE, PROMPTS, plausible  # noqa: E402
from nexusvox.llm import clean_reply  # noqa: E402
from nexusvox.polish import SYSTEM_PROMPT as POLISH_PROMPT  # noqa: E402
from nexusvox.polish import build_user_message  # noqa: E402

URL = "http://127.0.0.1:11434"
SHORT = "Ähm also ich wollte dir kurz sagen dass das Meeting äh morgen um 10 Uhr ist"
LONG = (
    "Also äh ich wollte nochmal kurz zusammenfassen was wir gestern besprochen haben. Wir haben uns darauf geeinigt "
    "dass der Release jetzt auf den fünfzehnten verschoben wird weil die Tests für den Export noch nicht ähm "
    "durchgelaufen sind. Anna kümmert sich um die Anpassung der Rechnungsvorlage und Ben schaut sich den Fehler im "
    "Upload an der immer dann auftritt wenn die Datei größer als zwanzig Megabyte ist. Ich hab außerdem mit dem Kunden "
    "gesprochen und der braucht bis Freitag eine erste Version damit er sie intern vorstellen kann. Wenn wir das nicht "
    "schaffen müssen wir ihm das so schnell wie möglich sagen. Und äh noch was zum Budget, die neuen Laptops kommen "
    "erst im November also müssen wir bis dahin mit den alten arbeiten."
)
SHORT_EN = "Um so I think we should uh we should ship it tomorrow"
POLISH_SAMPLES = [
    (
        "Ich wollte fragen ob du vielleicht morgen zeit hättest wir müssten das projekt besprechen weil es ist wichtig",
        "Make it more formal",
    ),
    (
        "hey can u send me the report asap i need it for the meeting its kind of urgent",
        "Make it polite and professional",
    ),
    ("Der Server ist ausgefallen und wir wissen noch nicht warum Ben schaut es sich an", "Fasse das kürzer"),
    ("We shipped the update yesterday and there were some bugs but we fixed most of them already", "Improve this text"),
]
SUMMARY_SYSTEM = (
    "You summarise the transcript of a team meeting. Write the summary in the language of the transcript. "
    "Give: the decisions made, the action items with the person responsible and any date, and the open questions. "
    "At most 200 words. Use only what the transcript says."
)
CTX_DEFAULT = 4096


def chat(model: str, system: str, user: str, *, cpu: bool, num_ctx: int = CTX_DEFAULT, keep_alive: str = "10m") -> dict:
    options: dict = {"temperature": 0.2, "num_ctx": num_ctx}
    if cpu:
        options["num_gpu"] = 0
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "think": False,
        "keep_alive": keep_alive,
        "options": options,
    }
    request = urllib.request.Request(f"{URL}/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=3600) as response:
        reply = json.load(response)
    reply["wall_s"] = time.perf_counter() - started
    return reply


def unload(model: str) -> None:
    subprocess.run(["ollama", "stop", model], capture_output=True, check=False)
    time.sleep(3)


def vram_used_mib() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True
    )
    return int(out.stdout.split()[0])


def ollama_rss_mib() -> int:
    total = 0
    for proc in psutil.process_iter(["name", "memory_info"]):
        if (proc.info["name"] or "").lower().startswith("ollama") and proc.info["memory_info"]:
            total += proc.info["memory_info"].rss
    return total // (1024 * 1024)


def ollama_ps(model: str) -> str:
    out = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith(model):
            return " | ".join(line.split("  ")[0:1] + [p.strip() for p in line.split("  ") if p.strip()][2:4])
    return "?"


def median(values: list[float]) -> float:
    return round(statistics.median(values), 2)


def timed(model: str, system: str, user: str, cpu: bool, reps: int) -> dict:
    runs = [chat(model, system, user, cpu=cpu) for _ in range(reps)]
    return {
        "median_s": median([r["wall_s"] for r in runs]),
        "all_s": [round(r["wall_s"], 2) for r in runs],
        "prompt_tokens": runs[-1].get("prompt_eval_count"),
        "reply_tokens": runs[-1].get("eval_count"),
        "tok_per_s": round(runs[-1]["eval_count"] / (runs[-1]["eval_duration"] / 1e9), 1)
        if runs[-1].get("eval_duration")
        else None,
        "reply": runs[-1]["message"]["content"].strip(),
    }


def quality(model: str, cpu: bool) -> dict:
    result: dict = {"cleanup": {}, "polish": []}
    for level in sorted(PROMPTS):
        rows = []
        for dictation, keep, drop in SAMPLES:
            reply = chat(model, PROMPTS[level], f"<dictation>{dictation}</dictation>", cpu=cpu)
            text = _TAG_RE.sub("", clean_reply(reply["message"]["content"])).strip()
            problems = []
            if not plausible(dictation, text, level):
                problems.append("rejected by the plausibility guard")  # the app would type the dictation unchanged
            else:
                problems += [f"lost {w!r}" for w in keep if w.lower() not in text.lower()]
                words = text.replace(",", " ").replace(".", " ").split()
                problems += [f"kept {w!r}" for w in drop if w in words]
            rows.append({"in": dictation, "out": text, "problems": problems, "s": round(reply["wall_s"], 2)})
        result["cleanup"][level] = {"failed": sum(bool(r["problems"]) for r in rows), "total": len(rows), "rows": rows}
    for selection, instruction in POLISH_SAMPLES:
        reply = chat(model, POLISH_PROMPT, build_user_message(selection, instruction), cpu=cpu)
        result["polish"].append(
            {
                "in": selection,
                "instruction": instruction,
                "out": clean_reply(reply["message"]["content"]),
                "s": round(reply["wall_s"], 2),
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--mode", choices=("gpu", "cpu"), required=True)
    parser.add_argument("--meeting", type=Path, help="transcript text file for the summary run")
    parser.add_argument("--summary-ctx", type=int, default=16384)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cpu = args.mode == "cpu"
    model = args.model
    res: dict = {"model": model, "mode": args.mode, "cpu_threads": psutil.cpu_count(logical=True)}

    unload(model)
    base_vram, base_rss = vram_used_mib(), ollama_rss_mib()
    reply = chat(model, "Reply with OK.", "OK", cpu=cpu)
    res["load"] = {"wall_s": round(reply["wall_s"], 2), "load_s": round(reply.get("load_duration", 0) / 1e9, 2)}
    time.sleep(1)
    res["memory_loaded"] = {
        "vram_mib": vram_used_mib() - base_vram,
        "ollama_rss_mib": ollama_rss_mib() - base_rss,
        "ollama_ps": ollama_ps(model),
    }
    print("load", res["load"], res["memory_loaded"], flush=True)

    jobs = {
        "cleanup_medium_short_de": (PROMPTS["medium"], f"<dictation>{SHORT}</dictation>"),
        "cleanup_medium_short_en": (PROMPTS["medium"], f"<dictation>{SHORT_EN}</dictation>"),
        "cleanup_light_short_de": (PROMPTS["light"], f"<dictation>{SHORT}</dictation>"),
        "cleanup_medium_long_de": (PROMPTS["medium"], f"<dictation>{LONG}</dictation>"),
        "cleanup_light_long_de": (PROMPTS["light"], f"<dictation>{LONG}</dictation>"),
        "polish_short": (POLISH_PROMPT, build_user_message(SHORT, "")),
        "polish_long": (POLISH_PROMPT, build_user_message(LONG, "Make it shorter")),
    }
    res["latency"] = {}
    for name, (system, user) in jobs.items():
        res["latency"][name] = timed(model, system, user, cpu, args.reps)
        print(name, {k: v for k, v in res["latency"][name].items() if k != "reply"}, flush=True)

    if args.meeting:
        transcript = args.meeting.read_text(encoding="utf-8")
        run = chat(model, SUMMARY_SYSTEM, transcript, cpu=cpu, num_ctx=args.summary_ctx)
        res["summary"] = {
            "words": len(transcript.split()),
            "wall_s": round(run["wall_s"], 1),
            "load_s": round(run.get("load_duration", 0) / 1e9, 1),
            "prompt_tokens": run.get("prompt_eval_count"),
            "prefill_s": round(run.get("prompt_eval_duration", 0) / 1e9, 1),
            "reply_tokens": run.get("eval_count"),
            "gen_s": round(run.get("eval_duration", 0) / 1e9, 1),
            "num_ctx": args.summary_ctx,
            "vram_mib": vram_used_mib() - base_vram,
            "ollama_rss_mib": ollama_rss_mib() - base_rss,
            "ollama_ps": ollama_ps(model),
            "reply": run["message"]["content"].strip(),
        }
        print("summary", {k: v for k, v in res["summary"].items() if k != "reply"}, flush=True)

    if not args.skip_quality:
        res["quality"] = quality(model, cpu)
        for level, q in res["quality"]["cleanup"].items():
            print("quality", level, f"{q['failed']}/{q['total']} with problems", flush=True)

    unload(model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
