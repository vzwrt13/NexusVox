"""Photograph Pip's daily speech bubble on Home, from the real scripts and style sheet. Two images:

    python scripts/pip_greetings.py [--out docs/images/pip-greetings.png]
        [--variants-out docs/images/pip-bubble-variants.png] [--browser PATH] [--scale 2]

`pip-greetings.png`: one bubble per kind of content (a milestone, a tip about a feature never used, a usage statistic),
each chosen by the real `pickPipGreeting` from invented counts and built by the real `buildPipBubble`.

`pip-bubble-variants.png`: three looks for the same bubble side by side, to choose from. A is what the dashboard ships;
B and C are drawn only here, from the same style sheet plus a few page-local rules.

A throwaway page loads `style.css`, `dashboard-pip.js`, `dashboard-pip-texts.js` and `dashboard-pip-greeting.js` from
the dashboard's static folder. Nothing here touches your config, your database, the browser's saved state or the
network, and the browser is headless: no window opens. The idle motion and the entrance are switched off for the
picture, so two runs give the same image.
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
IMAGES = _SCRIPTS.parent / "docs" / "images"
DEFAULT_OUT = IMAGES / "pip-greetings.png"
DEFAULT_VARIANTS_OUT = IMAGES / "pip-bubble-variants.png"
CONTENT_HEIGHT = 545
VARIANTS_HEIGHT = 250

# Invented counts for each kind of bubble. `seen` is what Pip has said before, so the next one is the wanted kind.
_QUIET = {"week_words": 0, "week_time_saved_minutes": 0, "typing_wpm": 40}
SAMPLES = (
    {
        "label": "Milestone",
        "facts": {
            "words": 10240,
            "dictations": 212,
            "meetings": 3,
            "uploads": 2,
            "dictionary_entries": 14,
            "formatting_commands": 1,
            **_QUIET,
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
            "formatting_commands": 1,
            **_QUIET,
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
            "formatting_commands": 1,
            "week_words": 3420,
            "week_time_saved_minutes": 61.5,
            "typing_wpm": 40,
        },
        "seen": ["first-dictation", "first-meeting", "first-file", "words-1000", "dictations-100"],
    },
)

# The three looks of the variants image: what the page draws for each.
VARIANTS = (
    {"key": "a", "label": "A  Quiet card, × in the corner (shipped)"},
    {"key": "b", "label": "B  Same card, a “Got it” link under the text"},
    {"key": "c", "label": "C  Same card, a small label line and ×"},
)
VARIANT_SAMPLE = SAMPLES[0]  # the same invented milestone in all three

HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="{style}">
<style>
  .pip-art, .pip-eyes-open, .pip-eyes-closed, .pip-bubble {{ animation: none !important; }}
  body {{ display: block; padding: 28px; }}
  h1 {{ font-size: 15px; font-weight: 600; color: var(--text-dim); margin-bottom: 20px; }}
  .cell {{ padding: 16px 22px; background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); }}
  .cell > span {{ display: block; margin-bottom: 8px; font-size: 12px; color: var(--text-dim); }}
  .cell .corner {{ display: flex; justify-content: flex-end; }}
  {extra_css}
</style></head>
<body>
"""

SCRIPTS = """<script src="{pip}"></script>
<script src="{texts}"></script>
<script src="{greeting}"></script>
"""

CONTENT_PAGE = (
    HEAD
    + """<h1>Pip's daily speech bubble on Home: a milestone, a tip, a statistic (invented sample data)</h1>
<div id="rows"></div>
"""
    + SCRIPTS
    + """<script>
  const rows = document.getElementById("rows");
  for (const sample of {samples}) {{
    const cell = document.createElement("div");
    cell.className = "cell";
    cell.style.marginBottom = "16px";
    const label = document.createElement("span");
    label.textContent = sample.label;
    const corner = document.createElement("div");
    corner.className = "corner";
    const host = document.createElement("div");
    host.className = "home-pip";
    corner.append(host);
    cell.append(label, corner);
    rows.append(cell);
    const state = {{ ...emptyPipGreetingState(), seen: sample.seen }};
    const {{ greeting }} = pickPipGreeting(sample.facts, state, "2026-11-04");
    mountPip(host, {{ pose: "tilt", side: "left", bubble: buildPipBubble(greeting.text, () => {{}}) }});
  }}
</script>
</body></html>
"""
)

# B and C are drawn here only: the same card, with the "Got it" link or a label line. The page-local rules adapt the
# shipped card (it reserves room for the × on its right).
VARIANTS_CSS = """
  .cells {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }}
  .cell .home-pip {{ margin: 0; padding: 0; }}
  .v-b .pip-bubble {{ padding-right: 14px; }}
  .v-b .got-it {{ display: block; margin-top: 8px; font-size: 12px; }}
  .v-c .eyebrow {{ display: block; margin-bottom: 3px; font-size: 10.5px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--accent-light); }}
"""

VARIANTS_PAGE = (
    HEAD
    + """<h1>Pip's bubble, three looks for the same milestone (invented sample data); A is what ships</h1>
<div class="cells" id="cells"></div>
"""
    + SCRIPTS
    + """<script>
  const sample = {sample};
  const state = {{ ...emptyPipGreetingState(), seen: sample.seen }};
  const {{ greeting }} = pickPipGreeting(sample.facts, state, "2026-11-04");
  const cells = document.getElementById("cells");
  const make = (tag, className, text) => {{
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }};
  const bubbleFor = (key) => {{
    if (key === "a") return buildPipBubble(greeting.text, () => {{}});
    const wrap = make("div");
    if (key === "b") {{
      wrap.append(make("span", "", greeting.text), make("button", "link-btn got-it", "Got it"));
    }} else {{
      const close = make("button", "pip-bubble-close", "\\u00d7");
      wrap.append(make("span", "eyebrow", "Milestone"), make("span", "", greeting.text), close);
    }}
    return wrap;
  }};
  for (const variant of {variants}) {{
    const cell = make("div", "cell v-" + variant.key);
    const corner = make("div", "corner");
    const host = make("div", "home-pip");
    corner.append(host);
    cell.append(make("span", "", variant.label), corner);
    cells.append(cell);
    mountPip(host, {{ pose: "tilt", side: "left", bubble: bubbleFor(variant.key) }});
  }}
</script>
</body></html>
"""
)


def _uris() -> dict[str, str]:
    return {
        "style": (STATIC / "style.css").as_uri(),
        "pip": (STATIC / "dashboard-pip.js").as_uri(),
        "texts": (STATIC / "dashboard-pip-texts.js").as_uri(),
        "greeting": (STATIC / "dashboard-pip-greeting.js").as_uri(),
    }


def build_pages(directory: Path) -> tuple[Path, Path]:
    """Write the two throwaway pages into `directory` and return their paths (content kinds, variants)."""
    content = directory / "pip-greetings.html"
    content.write_text(
        CONTENT_PAGE.format(**_uris(), extra_css="", samples=json.dumps(list(SAMPLES))),
        encoding="utf-8",
    )
    variants = directory / "pip-bubble-variants.html"
    variants.write_text(
        VARIANTS_PAGE.format(
            **_uris(),
            extra_css=VARIANTS_CSS.format(),
            sample=json.dumps(VARIANT_SAMPLE),
            variants=json.dumps(list(VARIANTS)),
        ),
        encoding="utf-8",
    )
    return content, variants


def run(out: Path, variants_out: Path, browser: str | None, scale: float) -> list[Path]:
    browser_path = _find_browser(browser)
    written = []
    with tempfile.TemporaryDirectory(prefix="nexusvox-pip-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        pages = build_pages(work)
        for page, destination, height in ((pages[0], out, CONTENT_HEIGHT), (pages[1], variants_out, VARIANTS_HEIGHT)):
            destination = destination.resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            target = work / destination.name
            _photograph(browser_path, page.as_uri(), target, work / f"browser-profile-{page.stem}", scale, height)
            destination.write_bytes(target.read_bytes())
            print(f"wrote {destination}")
            written.append(destination)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"content kinds image (default: {DEFAULT_OUT})")
    parser.add_argument(
        "--variants-out",
        type=Path,
        default=DEFAULT_VARIANTS_OUT,
        help=f"bubble variants image (default: {DEFAULT_VARIANTS_OUT})",
    )
    parser.add_argument("--browser", help="path or name of msedge or chrome (default: found automatically)")
    parser.add_argument("--scale", type=float, default=2.0, help="device pixel ratio of the image (default: 2)")
    args = parser.parse_args(argv)
    run(args.out, args.variants_out, args.browser, args.scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
