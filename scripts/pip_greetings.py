"""Photograph Pip's daily speech bubble on Home, once per kind of content, from the real scripts and style sheet.

    python scripts/pip_greetings.py [--out docs/images/pip-greetings.png] [--browser PATH] [--scale 2]

A throwaway page loads `style.css`, `dashboard-pip.js`, `dashboard-pip-texts.js` and `dashboard-pip-greeting.js`
from the dashboard's static folder and lets `pickPipGreeting` choose the bubble for three sets of invented counts:
a milestone, a tip about a feature never used and a statistic of the week. Nothing here touches your config, your
database, the browser's saved state or the network, and the browser is headless: no window opens. The idle motion
is switched off for the picture, so two runs give the same image.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

from dashboard_screenshots import _find_browser, _photograph  # noqa: E402  (the same headless browser setup)

STATIC = _SCRIPTS.parent / "src" / "nexusvox" / "dashboard" / "static"
DEFAULT_OUT = _SCRIPTS.parent / "docs" / "images" / "pip-greetings.png"
HEIGHT = 545

# Invented counts for each kind of bubble. `seen` is what Pip has said before, so the next one is the wanted kind.
SAMPLES = (
    {
        "label": "Milestone",
        "facts": {
            "words": 10240,
            "dictations": 212,
            "meetings": 3,
            "uploads": 2,
            "dictionary_entries": 14,
            "voice_commands": 31,
            "week_words": 0,
            "week_time_saved_minutes": 0,
            "typing_wpm": 40,
        },
        "seen": ["first-dictation", "first-meeting", "first-file", "words-1000", "dictations-100"],
    },
    {
        "label": "Tip about a feature never used",
        "facts": {
            "words": 4210,
            "dictations": 87,
            "meetings": 2,
            "uploads": 0,
            "dictionary_entries": 6,
            "voice_commands": 12,
            "week_words": 0,
            "week_time_saved_minutes": 0,
            "typing_wpm": 40,
        },
        "seen": ["first-dictation", "first-meeting", "words-1000"],
    },
    {
        "label": "Usage statistic",
        "facts": {
            "words": 6120,
            "dictations": 140,
            "meetings": 2,
            "uploads": 4,
            "dictionary_entries": 9,
            "voice_commands": 20,
            "week_words": 3420,
            "week_time_saved_minutes": 61.5,
            "typing_wpm": 40,
        },
        "seen": ["first-dictation", "first-meeting", "first-file", "words-1000", "dictations-100"],
    },
)

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="{style}">
<style>
  .pip-art, .pip-eyes-open, .pip-eyes-closed {{ animation: none !important; }}
  body {{ display: block; padding: 28px; }}
  h1 {{ font-size: 15px; font-weight: 600; color: var(--text-dim); margin-bottom: 20px; }}
  .cell {{ margin-bottom: 16px; padding: 16px 22px; background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); }}
  .cell span {{ display: block; margin-bottom: 8px; font-size: 12px; color: var(--text-dim); }}
  .home-pip {{ margin: 0; }}
  body {{ max-width: 760px; }}
</style></head>
<body>
<h1>Pip's daily speech bubble on Home: a milestone, a tip, a statistic (invented sample data)</h1>
<div id="rows"></div>
<script src="{pip}"></script>
<script src="{texts}"></script>
<script src="{greeting}"></script>
<script>
  const rows = document.getElementById("rows");
  for (const sample of {samples}) {{
    const cell = document.createElement("div");
    cell.className = "cell";
    const label = document.createElement("span");
    label.textContent = sample.label;
    const host = document.createElement("div");
    host.className = "home-pip";
    cell.append(label, host);
    rows.append(cell);
    const state = {{ ...emptyPipGreetingState(), seen: sample.seen }};
    const {{ greeting }} = pickPipGreeting(sample.facts, state, "2026-11-04");
    mountPip(host, {{ pose: "tilt", bubble: buildPipBubble(greeting.text, () => {{}}) }});
  }}
</script>
</body></html>
"""


def build_page(directory: Path) -> Path:
    """Write the throwaway page into `directory` and return its path."""
    page = directory / "pip-greetings.html"
    page.write_text(
        PAGE.format(
            style=(STATIC / "style.css").as_uri(),
            pip=(STATIC / "dashboard-pip.js").as_uri(),
            texts=(STATIC / "dashboard-pip-texts.js").as_uri(),
            greeting=(STATIC / "dashboard-pip-greeting.js").as_uri(),
            samples=json.dumps(list(SAMPLES)),
        ),
        encoding="utf-8",
    )
    return page


def run(out: Path, browser: str | None, scale: float) -> Path:
    browser_path = _find_browser(browser)
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nexusvox-pip-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        page = build_page(work)
        target = work / out.name
        _photograph(browser_path, page.as_uri(), target, work / "browser-profile", scale, HEIGHT)
        out.write_bytes(target.read_bytes())
    print(f"wrote {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output image (default: {DEFAULT_OUT})")
    parser.add_argument("--browser", help="path or name of msedge or chrome (default: found automatically)")
    parser.add_argument("--scale", type=float, default=2.0, help="device pixel ratio of the image (default: 2)")
    args = parser.parse_args(argv)
    run(args.out, args.browser, args.scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
