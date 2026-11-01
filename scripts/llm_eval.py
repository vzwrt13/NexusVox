"""Quality and speed of the local language model on 112 Cleanup/Polish items and real meeting transcripts.

Three steps, each writes a JSON file under `--dir` (default `benchmarks/llm`, git-ignored):

    python scripts/ami_fetch.py benchmarks/llm/ami                      # real meetings (AMI corpus, CC BY 4.0)
    python scripts/llm_eval.py gen --model gemma4:e2b --mode cpu        # replies + timing, `--reps` runs per item
    python scripts/llm_eval.py judge                                    # rubric score of every gen file
    python scripts/llm_eval.py language                                 # does a model translate English text?
    python scripts/llm_eval.py report                                   # markdown tables from the files

`gen` sends every item of `llm_eval_items` (60 dictations at both Cleanup levels, 52 Polish selections) and
every `ami/*.txt` transcript `--reps` times and records the wall time of each request. It sends nothing else
while it runs, so run it alone. `judge` loads the judge model once (`--judge`, default `gemma4:12b-it-qat`) and
scores the first reply of every item against a fixed rubric; it runs on the GPU and must not overlap a `gen`.
The prompts are the app's own (`cleanup.PROMPTS`, `polish.SYSTEM_PROMPT`); the summary prompt is the one of
`llm_bench.py`. The cleanup plausibility guard is applied as in the app and counted as "rejected".
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_bench import SUMMARY_SYSTEM, chat, looks_german, unload  # noqa: E402
from llm_eval_items import CLEANUP, POLISH  # noqa: E402

from nexusvox.cleanup import _TAG_RE, PROMPTS, plausible  # noqa: E402
from nexusvox.llm import clean_reply  # noqa: E402
from nexusvox.polish import SYSTEM_PROMPT as POLISH_PROMPT  # noqa: E402
from nexusvox.polish import build_user_message  # noqa: E402

JUDGE = "gemma4:12b-it-qat"
SUMMARY_CTX = 8192  # AMI transcripts of about 3,300 words are 4,500-5,500 tokens

CRITERIA = {
    "cleanup": {
        "meaning_kept": "Every fact, number, name and the meaning of the dictation are kept. A question or order in "
        "the "
        "dictation is still a question or order and was not answered or carried out.",
        "language_kept": "The reply is in the same language as the dictation, and nothing was translated.",
        "nothing_invented": "The reply adds no fact, word of commentary, greeting, quote or placeholder.",
        "formatting": "Fillers (um, uh, äh, ähm, hm), false starts and accidental repetitions are removed, and "
        "punctuation and capitalisation are correct. The reply is only the cleaned text.",
    },
    "polish": {
        "meaning_kept": "The reply keeps the facts, numbers and names of the text, except where the instruction asks "
        "to "
        "shorten or summarise (then the main point must stay).",
        "language_kept": "The reply is in the language of the text, unless the instruction asks for a translation, "
        "in which case it is in the requested language.",
        "nothing_invented": "The reply adds no fact, commentary, greeting, quote or placeholder such as [Name].",
        "formatting": "The reply follows the instruction (tone, length, form) and is only the rewritten text.",
    },
    "summary": {
        "meaning_kept": "The decisions, action items and open questions it names are in the transcript, and the main "
        "ones are there.",
        "language_kept": "The summary is in the language of the transcript.",
        "nothing_invented": "No decision, person, number or date appears that the transcript does not contain.",
        "formatting": "It is at most about 200 words and organised as decisions, action items with owner, and open "
        "questions.",
    },
}
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        **{c: {"type": "integer", "enum": [0, 1]} for c in CRITERIA["cleanup"]},
        "reason": {"type": "string"},
    },
    "required": [*CRITERIA["cleanup"], "reason"],
}
JUDGE_SYSTEM = (
    "You are a strict reviewer who scores the output of a text tool against a fixed rubric. For each criterion "
    "give 1 if it is fully met and 0 if not. Judge only what is written. Reply with JSON only. "
    "The reason is one short sentence naming the worst problem, or 'ok'."
)


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value that at least a share `q` of the values do not exceed."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def summarise(times: list[float]) -> dict:
    return {
        "n": len(times),
        "median_s": round(statistics.median(times), 2),
        "p90_s": round(percentile(times, 0.9), 2),
        "mean_s": round(statistics.mean(times), 2),
    }


def per_item(runs: list[dict]) -> dict:
    # Ollama reuses the cached prompt of an identical request, so runs 2+ of a long summary skip the prefill;
    # the summary tables therefore use the first run, `times[0]`.
    times = [r["wall_s"] for r in runs]
    return {"times": [round(t, 2) for t in times], "median_s": round(statistics.median(times), 2)}


def run_item(model: str, cpu: bool, system: str, user: str, reps: int, ctx: int = 4096) -> tuple[list[dict], str]:
    runs = [chat(model, system, user, cpu=cpu, num_ctx=ctx) for _ in range(reps)]
    return runs, runs[0]["message"]["content"]


def gen_summary(args: argparse.Namespace, model: str, cpu: bool) -> list[dict]:
    """Summarise every AMI transcript `--reps` times. The model is first reloaded at the summary context size,
    so the first meeting's first run does not pay for the reload."""
    chat(model, "Reply with OK.", "OK", cpu=cpu, num_ctx=SUMMARY_CTX)
    rows = []
    for path in sorted((args.dir / "ami").glob("*.txt")):
        transcript = path.read_text(encoding="utf-8")
        if not transcript.strip():
            raise SystemExit(f"{path} is empty; run scripts/ami_fetch.py again")
        runs, raw = run_item(model, cpu, SUMMARY_SYSTEM, transcript, args.reps, SUMMARY_CTX)
        rows.append(
            {
                "meeting": path.stem,
                "words": len(transcript.split()),
                "prompt_tokens": runs[0].get("prompt_eval_count"),
                "reply_tokens": runs[0].get("eval_count"),
                "load_s": round(runs[0].get("load_duration", 0) / 1e9, 2),
                "prefill_s": round(runs[0].get("prompt_eval_duration", 0) / 1e9, 1),
                "gen_s": round(runs[0].get("eval_duration", 0) / 1e9, 1),
                "out": raw.strip(),
                **per_item(runs),
            }
        )
        print("summary", path.stem, rows[-1]["times"], flush=True)
    return rows


def cmd_gen(args: argparse.Namespace) -> None:
    cpu = args.mode == "cpu"
    model = args.model
    unload(model)
    target = args.dir / f"gen-{model.replace(':', '_')}-{args.mode}.json"
    if args.summary_only:  # redo only the summaries of an existing file; their verdicts are dropped
        out = json.loads(target.read_text(encoding="utf-8"))
        out["summary"] = gen_summary(args, model, cpu)
        out.pop("judged_by", None)
        unload(model)
        target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print("wrote", target)
        return
    chat(model, "Reply with OK.", "OK", cpu=cpu)  # load, so no item pays for it
    out: dict = {"model": model, "mode": args.mode, "reps": args.reps, "cleanup": {}, "polish": [], "summary": []}
    started = time.time()

    for level in sorted(PROMPTS):
        rows = []
        for lang, text in CLEANUP:
            runs, raw = run_item(model, cpu, PROMPTS[level], f"<dictation>{text}</dictation>", args.reps)
            reply = _TAG_RE.sub("", clean_reply(raw)).strip()
            rows.append(
                {
                    "lang": lang,
                    "in": text,
                    "out": reply,
                    "rejected": not plausible(text, reply, level),
                    **per_item(runs),
                    "reply_tokens": runs[0].get("eval_count"),
                }
            )
        out["cleanup"][level] = rows
        print(level, summarise([t for r in rows for t in r["times"]]), flush=True)

    for lang, text, instruction in POLISH:
        runs, raw = run_item(model, cpu, POLISH_PROMPT, build_user_message(text, instruction), args.reps)
        out["polish"].append(
            {
                "lang": lang,
                "in": text,
                "instruction": instruction,
                "out": clean_reply(raw),
                **per_item(runs),
                "reply_tokens": runs[0].get("eval_count"),
            }
        )
    print("polish", summarise([t for r in out["polish"] for t in r["times"]]), flush=True)

    out["summary"] = gen_summary(args, model, cpu)

    out["duration_min"] = round((time.time() - started) / 60, 1)
    unload(model)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", target, out["duration_min"], "min")


def judge_one(judge: str, kind: str, source: str, reply: str, extra: str = "") -> dict:
    rubric = "\n".join(f"- {name}: {text}" for name, text in CRITERIA[kind].items())
    user = (
        f"Rubric:\n{rubric}\n\n{extra}"
        f"Input given to the tool:\n<input>\n{source}\n</input>\n\nOutput of the tool:\n<output>\n{reply}\n</output>"
    )
    try:
        reply_json = chat(judge, JUDGE_SYSTEM, user, cpu=False, num_ctx=12288, schema=VERDICT_SCHEMA, temperature=0.0)
        verdict = json.loads(reply_json["message"]["content"])
        return {c: int(verdict[c]) for c in CRITERIA[kind]} | {"reason": str(verdict.get("reason", ""))}
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        # A judge failure is not a model failure: the item is left out of the scores and counted apart.
        return {"error": f"{type(exc).__name__}: {exc}"}


def cmd_judge(args: argparse.Namespace) -> None:
    unload(args.judge)
    for path in sorted(args.dir.glob("gen-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("judged_by") == args.judge and not args.again:
            continue

        def todo(row: dict) -> bool:  # a verdict is kept unless --again; a judge error is retried
            return args.again or "verdict" not in row or "error" in row["verdict"]

        for rows in data["cleanup"].values():
            for row in filter(todo, rows):
                # The app types the dictation unchanged when the guard rejects a reply: score what it types.
                typed = row["in"] if row["rejected"] else row["out"]
                row["verdict"] = judge_one(args.judge, "cleanup", row["in"], typed)
        for row in filter(todo, data["polish"]):
            row["verdict"] = judge_one(
                args.judge, "polish", f"Instruction: {row['instruction']}\nText: {row['in']}", row["out"]
            )
        for row in filter(todo, data["summary"]):
            transcript = (args.dir / "ami" / f"{row['meeting']}.txt").read_text(encoding="utf-8")
            row["verdict"] = judge_one(args.judge, "summary", transcript, row["out"])
        data["judged_by"] = args.judge
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print("judged", path.name, flush=True)
    unload(args.judge)


# The German equivalent of every English Polish instruction that is not a translation request.
GERMAN_INSTRUCTION = {
    "Correct spelling and punctuation": "Korrigiere Rechtschreibung und Zeichensetzung",
    "Cut it down to the essentials": "Kürze auf das Wesentliche",
    "Fix capitalisation and punctuation": "Korrigiere Großschreibung und Zeichensetzung",
    "Fix only grammar and punctuation": "Behebe nur Grammatik und Zeichensetzung",
    "Improve the style": "Verbessere den Stil",
    "Improve the text": "Verbessere den Text",
    "Improve this text": "Verbessere diesen Text",
    "Make it friendlier": "Mach es freundlicher",
    "Make it less apologetic": "Mach es weniger entschuldigend",
    "Make it less stiff": "Mach es weniger steif",
    "Make it more detailed": "Mach es ausführlicher",
    "Make it more formal": "Mach es formeller",
    "Make it more neutral": "Mach es neutraler",
    "Make it more polite": "Mach es höflicher",
    "Make it more professional": "Mach es professioneller",
    "Make it polite and professional": "Mach es höflich und professionell",
    "Make it shorter": "Mach es kürzer",
    "Phrase this as constructive criticism": "Formuliere das als konstruktive Kritik",
    "Summarise this in one sentence": "Fasse das in einem Satz zusammen",
    "Turn this into a task list": "Mach daraus eine Aufgabenliste",
    "Turn this into an invitation": "Mach daraus eine Einladung",
    "Turn this into bullet points": "Schreib das als Stichpunkte",
    "Write it as full sentences": "Schreibe es in vollständigen Sätzen",
    "Write it for a customer without technical terms": "Schreibe es für einen Kunden ohne Fachbegriffe",
}


# English texts of the Polish set for the translation check, with three kinds of instruction.
def cmd_language(args: argparse.Namespace) -> None:
    english = [(t, i) for lang, t, i in POLISH if lang == "en" and "Translate" not in i]
    out: dict = {}
    for model in args.models:
        unload(model)
        out[model] = {}
        for variant in ("english_instruction", "german_instruction", "default_instruction"):
            german, replies = 0, []
            for text, instruction in english:
                if variant == "german_instruction":
                    instruction = GERMAN_INSTRUCTION[instruction]
                elif variant == "default_instruction":
                    instruction = ""
                reply = clean_reply(
                    chat(model, POLISH_PROMPT, build_user_message(text, instruction), cpu=False)["message"]["content"]
                )
                german += looks_german(reply)
                replies.append({"in": text, "instruction": instruction, "out": reply, "german": looks_german(reply)})
            out[model][variant] = {"german": german, "of": len(english), "rows": replies}
            print(model, variant, f"{german}/{len(english)} came back German", flush=True)
        unload(model)
    (args.dir / "language.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def score(rows: list[dict], kind: str) -> dict:
    verdicts = [r["verdict"] for r in rows if "verdict" in r and "error" not in r["verdict"]]
    result = {c: round(100 * statistics.mean(v[c] for v in verdicts), 1) for c in CRITERIA[kind]}
    result["all_pass"] = round(100 * statistics.mean(all(v[c] for c in CRITERIA[kind]) for v in verdicts), 1)
    result["rubric"] = round(statistics.mean(result[c] for c in CRITERIA[kind]), 1)
    return result


def cmd_report(args: argparse.Namespace) -> None:
    files = {}
    for path in sorted(args.dir.glob("gen-*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        files[(d["model"], d["mode"])] = d
    lines = ["# speed (wall time per request, all runs of all items)", ""]
    lines.append("| Job | Model | Mode | n | median | p90 | median 1st run | p90 1st run | reply tokens |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for (model, mode), d in files.items():
        jobs = {f"Cleanup {lvl}": rows for lvl, rows in d["cleanup"].items()} | {"Polish": d["polish"]}
        for name, rows in jobs.items():
            for lang in ("de", "en"):
                sel = [r for r in rows if r["lang"] == lang]
                s = summarise([t for r in sel for t in r["times"]])
                first = summarise([r["times"][0] for r in sel])
                tok = statistics.median(r["reply_tokens"] or 0 for r in sel)
                lines.append(
                    f"| {name} {lang.upper()} | {model} | {mode} | {s['n']} | {s['median_s']} | {s['p90_s']} "
                    f"| {first['median_s']} | {first['p90_s']} | {tok:.0f} |"
                )
        for r in d["summary"]:
            lines.append(
                f"| Summary {r['meeting']} ({r['prompt_tokens']} tok, first run) | {model} | {mode} | 1 | "
                f"{r['times'][0]} | - | {r['times'][0]} | - | {r['reply_tokens']} |"
            )
    lines += ["", "# quality (percent of items meeting each criterion; judge reply on the first run)", ""]
    lines.append(
        "| Job | Model | Mode | n | meaning | language | invented-nothing | formatting | rubric | all 4 "
        "| rejected by guard | judge errors |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (model, mode), d in files.items():
        jobs = {f"Cleanup {lvl}": ("cleanup", rows) for lvl, rows in d["cleanup"].items()}
        jobs["Polish"] = ("polish", d["polish"])
        jobs["Summary"] = ("summary", d["summary"])
        for name, (kind, rows) in jobs.items():
            if not any("verdict" in r for r in rows):
                continue
            s = score(rows, kind)
            rejected = sum(r.get("rejected", False) for r in rows) if kind == "cleanup" else "-"
            lines.append(
                f"| {name} | {model} | {mode} | {len(rows)} | {s['meaning_kept']} | {s['language_kept']} | "
                f"{s['nothing_invented']} | {s['formatting']} | {s['rubric']} | {s['all_pass']} | {rejected} | "
                f"{sum('error' in r.get('verdict', {}) for r in rows)} |"
            )
    (args.dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def cmd_flag(args: argparse.Namespace) -> None:
    """Pick 20 items for the user's spot check: the worst judged ones, spread over models, plus a few passes."""
    pool = []
    for path in sorted(args.dir.glob("gen-*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        for level, rows in d["cleanup"].items():
            pool += [("Cleanup " + level, d, r) for r in rows]
        pool += [("Polish", d, r) for r in d["polish"]]

    def points(p: tuple) -> int | None:  # criteria met out of 4; None when not judged or the judge failed
        verdict = p[2].get("verdict")
        return None if not verdict or "error" in verdict else sum(verdict[c] for c in CRITERIA["cleanup"])

    failed = sorted((p for p in pool if points(p) is not None and points(p) < 4), key=lambda p: (points(p), p[2]["in"]))
    passed = [p for p in pool if points(p) == 4]
    picked, seen = [], set()
    for p in failed:  # one item once, so the 20 cover 20 different inputs
        if p[2]["in"] not in seen and len(picked) < 15:
            picked.append((p, "judge found a problem")), seen.add(p[2]["in"])
    for p in passed[:: max(1, len(passed) // 5)]:
        if p[2]["in"] not in seen and len(picked) < 20:
            picked.append((p, "judge passed it (check the judge)")), seen.add(p[2]["in"])
    lines = ["# Spot check: 20 items for a human read", ""]
    for n, ((name, d, r), why) in enumerate(picked, 1):
        v = r["verdict"]
        lines += [
            f"## {n}. {name}, `{d['model']}` on {d['mode']} ({why})",
            f"- Input: {r['in']}" + (f"\n- Instruction: {r['instruction']}" if "instruction" in r else ""),
            f"- Output: {r['out']}",
            f"- Judge: meaning {v['meaning_kept']}, language {v['language_kept']}, invented-nothing "
            f"{v['nothing_invented']}, formatting {v['formatting']} - {v['reason']}",
            "",
        ]
    (args.dir / "spot-check.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {len(picked)} items")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=Path("benchmarks/llm"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("gen")
    gen.add_argument("--model", required=True)
    gen.add_argument("--mode", choices=("gpu", "cpu"), required=True)
    gen.add_argument("--reps", type=int, default=3)
    gen.add_argument("--summary-only", action="store_true", help="redo only the summaries of an existing file")
    gen.set_defaults(func=cmd_gen)
    judge = sub.add_parser("judge")
    judge.add_argument("--judge", default=JUDGE)
    judge.add_argument("--again", action="store_true")
    judge.set_defaults(func=cmd_judge)
    language = sub.add_parser("language")
    language.add_argument("--models", nargs="+", default=["qwen3.5:4b", "gemma4:e2b", "gemma4:e4b"])
    language.set_defaults(func=cmd_language)
    sub.add_parser("report").set_defaults(func=cmd_report)
    sub.add_parser("flag").set_defaults(func=cmd_flag)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    args.dir.mkdir(parents=True, exist_ok=True)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
