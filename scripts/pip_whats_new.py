"""Photograph Pip's what's-new popup on Home, from the real scripts and style sheet:

    python scripts/pip_whats_new.py [--out docs/images/pip-whats-new.png] [--browser PATH] [--scale 2]

One image with the first and the last step of the popup, built by the real `buildPipTourStep` from invented sample
entries (the shipped data file has none until a release lists some). A throwaway page loads `style.css`,
`dashboard-pip.js`, `dashboard-pip-texts.js`, `dashboard-pip-greeting.js` and `dashboard-pip-tour.js` from the
dashboard's static folder.
Nothing here touches your config, your database, the browser's saved state or the network, and the browser is
headless: no window opens. The idle motion and the entrance are switched off, so two runs give the same image.
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
DEFAULT_OUT = _SCRIPTS.parent / "docs" / "images" / "pip-whats-new.png"
HEIGHT = 330

# Invented entries: the real ones are the user's pick for a release.
SAMPLE_STEPS = [
    {
        "title": "Spoken formatting",
        "text": 'Say "new paragraph" while you dictate and the text breaks there.',
        "target": "settings",
    },
    {
        "title": "Your own words",
        "text": "Add names and terms under Vocabulary so they come out spelled right.",
        "target": "settings/vocabulary",
    },
    {"title": "Meetings", "text": "Record both sides of a call and read the transcript afterwards."},
]

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="{style}">
<style>
  .pip-art, .pip-eyes-open, .pip-eyes-closed, .pip-bubble {{ animation: none !important; }}
  body {{ display: block; padding: 28px; }}
  h1 {{ font-size: 15px; font-weight: 600; color: var(--text-dim); margin-bottom: 20px; }}
  .cells {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
  .cell {{ padding: 16px 22px; background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); }}
  .cell > span {{ display: block; margin-bottom: 8px; font-size: 12px; color: var(--text-dim); }}
  .cell .corner {{ display: flex; justify-content: flex-end; }}
</style></head>
<body>
<h1>Pip's what's-new popup after an update: first and last step (invented sample entries)</h1>
<div class="cells" id="cells"></div>
<script src="{pip}"></script>
<script src="{texts}"></script>
<script src="{greeting}"></script>
<script src="{tour}"></script>
<script>
  const steps = {steps};
  const cells = document.getElementById("cells");
  const labels = ["Step 1 of 3", "Step 3 of 3 (the last)"];
  [0, steps.length - 1].forEach((index, n) => {{
    const cell = document.createElement("div");
    cell.className = "cell";
    const label = document.createElement("span");
    label.textContent = labels[n];
    const corner = document.createElement("div");
    corner.className = "corner";
    const host = document.createElement("div");
    host.className = "home-pip";
    corner.append(host);
    cell.append(label, corner);
    cells.append(cell);
    const noop = () => {{}};
    mountPip(host, {{ pose: "tilt", side: "left", bubble: buildPipTourStep(steps, index,
      {{ onNext: noop, onClose: noop, onShow: noop }}) }});
  }});
</script>
</body></html>
"""


def run(out: Path, browser: str | None, scale: float) -> Path:
    browser_path = _find_browser(browser)
    with tempfile.TemporaryDirectory(prefix="nexusvox-pip-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        page = work / "pip-whats-new.html"
        page.write_text(
            PAGE.format(
                style=(STATIC / "style.css").as_uri(),
                pip=(STATIC / "dashboard-pip.js").as_uri(),
                texts=(STATIC / "dashboard-pip-texts.js").as_uri(),
                greeting=(STATIC / "dashboard-pip-greeting.js").as_uri(),
                tour=(STATIC / "dashboard-pip-tour.js").as_uri(),
                steps=json.dumps(SAMPLE_STEPS),
            ),
            encoding="utf-8",
        )
        destination = out.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = work / destination.name
        _photograph(browser_path, page.as_uri(), target, work / "browser-profile", scale, HEIGHT)
        destination.write_bytes(target.read_bytes())
    print(f"wrote {destination}")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"image path (default: {DEFAULT_OUT})")
    parser.add_argument("--browser", help="path or name of msedge or chrome (default: found automatically)")
    parser.add_argument("--scale", type=float, default=2.0, help="device pixel ratio of the image (default: 2)")
    args = parser.parse_args(argv)
    run(args.out, args.browser, args.scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
