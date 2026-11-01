"""Measure a local Ollama model on the NexusVox language-model jobs: load time, Auto Cleanup and Polish
latency for a short and a long dictation, a meeting summary, memory use, and reply quality.

    python scripts/llm_bench.py --model gemma4:e2b --mode gpu --out results/gemma4-e2b-gpu.json
    python scripts/llm_bench.py --model gemma4:e2b --mode cpu --meeting meeting_de.txt --skip-quality \
        --out results/gemma4-e2b-cpu.json

`--meeting` names a transcript file; if it does not exist it is generated first (`--meeting-model`, default
`gemma4:12b`, must be pulled): about 5,300 words of German team talk, which is a synthetic stand-in for a
35-minute meeting. RAM figures need `pip install psutil`; VRAM needs `nvidia-smi` (GPU mode only).

`--mode cpu` sends `num_gpu = 0`, so Ollama keeps every layer on the CPU. The prompts are the app's own
(`cleanup.PROMPTS`, `polish.SYSTEM_PROMPT`) and the cleanup checks are `cleanup_eval._check`: a reply the
plausibility guard rejects is counted as "rejected" (the app types the dictation unchanged), not as a problem. The
meeting summary prompt is the benchmark's own: NexusVox has no summary feature yet. Needs a running Ollama;
the model must already be pulled. Nothing is typed or saved.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    import psutil
except ImportError:  # RAM and thread count are then left out of the results
    psutil = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from types import SimpleNamespace  # noqa: E402

from cleanup_eval import SAMPLES, _check  # noqa: E402

from nexusvox.cleanup import _TAG_RE, IMPLAUSIBLE, PROMPTS, plausible  # noqa: E402
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


def vram_used_mib() -> int | None:
    """Memory used on all NVIDIA GPUs together, or None without nvidia-smi or a GPU."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True
        ).stdout.split()
        return sum(int(v) for v in out) if out else None
    except (OSError, ValueError):
        return None


def ram_used_mib() -> int | None:
    """System memory in use (all processes), or None without psutil."""
    return psutil.virtual_memory().used // (1024 * 1024) if psutil else None


def delta(now: int | None, before: int | None) -> int | None:
    return None if now is None or before is None else now - before


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


GERMAN_WORDS = {"wir", "und", "der", "die", "das", "ist", "es", "haben", "gestern", "einige", "aber", "bereits"}
EN_SELECTION = "We shipped the update yesterday and there were some bugs but we fixed most of them already"


def looks_german(text: str) -> bool:
    return len(GERMAN_WORDS & set(re.findall(r"\w+", text.lower()))) >= 3


def language_check(model: str, cpu: bool, reps: int = 3) -> dict:
    """Does an English selection come back in English? Per instruction, how many of `reps` replies are German."""
    result = {}
    for label, instruction in (("short", "Improve this text"), ("default", "")):
        replies = [
            clean_reply(
                chat(model, POLISH_PROMPT, build_user_message(EN_SELECTION, instruction), cpu=cpu)["message"]["content"]
            )
            for _ in range(reps)
        ]
        result[label] = {"german": sum(looks_german(r) for r in replies), "of": reps, "replies": replies}
    return result


def quality(model: str, cpu: bool) -> dict:
    result: dict = {"cleanup": {}, "polish": []}
    for level in sorted(PROMPTS):
        rows = []
        for dictation, keep, drop in SAMPLES:
            reply = chat(model, PROMPTS[level], f"<dictation>{dictation}</dictation>", cpu=cpu)
            text = _TAG_RE.sub("", clean_reply(reply["message"]["content"])).strip()
            rejected = not plausible(dictation, text, level)  # the app then types the dictation unchanged
            outcome = SimpleNamespace(
                text=dictation if rejected else text,
                cleaned=not rejected and text != dictation,
                error=IMPLAUSIBLE if rejected else None,
            )
            problems = _check(dictation, outcome, keep, drop)
            rows.append(
                {
                    "in": dictation,
                    "out": text,
                    "rejected": rejected,
                    "problems": problems,
                    "s": round(reply["wall_s"], 2),
                }
            )
        result["cleanup"][level] = {
            "failed": sum(bool(r["problems"]) for r in rows),
            "rejected": sum(r["rejected"] for r in rows),
            "total": len(rows),
            "rows": rows,
        }
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
    result["polish_language"] = language_check(model, cpu)
    return result


def make_meeting(path: Path, model: str) -> None:
    """Write a synthetic German team-meeting transcript of about 5,300 words (six topic blocks)."""
    topics = [
        "Quartalsplanung und Prioritäten",
        "Fehlerbehebung beim Rechnungsexport",
        "Neukunden-Onboarding und Support-Tickets",
        "Budget und Hardware-Beschaffung",
        "Release-Termin und Testplan",
        "Datenschutz, Aufbewahrungsfristen und offene Fragen",
    ]
    blocks = []
    for topic in topics:
        prompt = (
            f"Schreibe das ungeschnittene Rohtranskript eines Teils einer deutschen Teambesprechung (Thema: {topic}). "
            "Vier Personen (Anna, Ben, Carla, David) reden durcheinander wie in echt: Füllwörter (äh, also, ähm), "
            "Satzabbrüche, Rückfragen, konkrete Zahlen, Daten, Namen und Entscheidungen, einige englische IT-Begriffe. "
            "Format pro Zeile 'Name: Satz'. Genau etwa 750 Wörter. Kein Kommentar, keine Überschrift, kein Markdown."
        )
        blocks.append(chat(model, "", prompt, cpu=False, keep_alive="0")["message"]["content"].strip())
    path.write_text("\n".join(blocks), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--mode", choices=("gpu", "cpu"), required=True)
    parser.add_argument("--meeting", type=Path, help="transcript text file for the summary run (generated if missing)")
    parser.add_argument("--meeting-model", default="gemma4:12b", help="model that writes a missing --meeting file")
    parser.add_argument("--summary-ctx", type=int, default=16384)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cpu = args.mode == "cpu"
    model = args.model
    res: dict = {"model": model, "mode": args.mode, "cpu_threads": psutil.cpu_count(logical=True) if psutil else None}

    unload(model)
    base_vram, base_ram = (None if cpu else vram_used_mib()), ram_used_mib()
    reply = chat(model, "Reply with OK.", "OK", cpu=cpu)
    res["load"] = {"wall_s": round(reply["wall_s"], 2), "load_s": round(reply.get("load_duration", 0) / 1e9, 2)}
    chat(model, PROMPTS["medium"], f"<dictation>{SHORT}</dictation>", cpu=cpu)
    time.sleep(2)
    res["memory_loaded"] = {
        "vram_mib": delta(None if cpu else vram_used_mib(), base_vram),
        "ram_mib": delta(ram_used_mib(), base_ram),
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
        if not args.meeting.exists():
            make_meeting(args.meeting, args.meeting_model)
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
            "vram_mib": delta(None if cpu else vram_used_mib(), base_vram),
            "ram_mib": delta(ram_used_mib(), base_ram),
            "ollama_ps": ollama_ps(model),
            "reply": run["message"]["content"].strip(),
        }
        print("summary", {k: v for k, v in res["summary"].items() if k != "reply"}, flush=True)

    if not args.skip_quality:
        res["quality"] = quality(model, cpu)
        for level, q in res["quality"]["cleanup"].items():
            print(
                "quality",
                level,
                f"{q['failed']}/{q['total']} with problems, {q['rejected']} rejected by the guard",
                flush=True,
            )

    unload(model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
