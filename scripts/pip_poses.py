"""Photograph Pip, the dashboard mascot, in every pose, from the real component and the real style sheet.

    python scripts/pip_poses.py [--out docs/images/pip-poses.png] [--browser PATH] [--scale 2]

A throwaway page loads `style.css` and `dashboard-pip.js` from the dashboard's static folder and mounts Pip once
per pose (idle, blink, head tilt, sleeping) plus once with a speech bubble. It also writes the empty Recordings list
with Pip sleeping next to the first image (`--out`'s folder) as `pip-empty-recordings.png`.
The bubble text is invented sample data, counts only.
Nothing here touches your config, your database or the network, and the browser is headless: no window opens.
The idle motion is switched off for the picture, so two runs give the same image.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

from dashboard_screenshots import _find_browser, _photograph  # noqa: E402  (the same headless browser setup)

STATIC = _SCRIPTS.parent / "src" / "nexusvox" / "dashboard" / "static"
DEFAULT_OUT = _SCRIPTS.parent / "docs" / "images" / "pip-poses.png"
EMPTY_OUT = _SCRIPTS.parent / "docs" / "images" / "pip-empty-recordings.png"
HEIGHT = 250

POSES = (("idle", "Idle"), ("blink", "Blink"), ("tilt", "Head tilt"), ("sleeping", "Sleeping"))
SAMPLE_BUBBLE = "42 dictations this week. Nice rhythm."

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="{style}">
<style>
  .pip-art, .pip-eyes-open, .pip-eyes-closed {{ animation: none !important; }}
  body {{ display: block; padding: 28px; }}
  h1 {{ font-size: 15px; font-weight: 600; color: var(--text-dim); margin-bottom: 20px; }}
  .row {{ display: flex; flex-wrap: wrap; gap: 28px; }}
  .cell {{ display: flex; flex-direction: column; align-items: center; gap: 10px; width: 150px; padding: 18px 0;
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); }}
  .cell span {{ font-size: 12px; color: var(--text-dim); }}
  .wide {{ width: auto; flex-direction: row; padding: 18px 22px; }}
</style></head>
<body>
<h1>Pip: idle, blink, head tilt, sleeping, and a speech bubble (invented sample data)</h1>
<div class="row" id="row"></div>
<script src="{script}"></script>
<script>
  const row = document.getElementById("row");
  const add = (cls, label, options) => {{
    const cell = document.createElement("div");
    cell.className = "cell " + cls;
    const host = document.createElement("div");
    cell.append(host);
    if (label) {{ const text = document.createElement("span"); text.textContent = label; cell.append(text); }}
    row.append(cell);
    mountPip(host, options);
  }};
  for (const [pose, label] of {poses}) add("", label, {{ pose }});
  add("wide", "", {{ pose: "idle", bubble: {bubble} }});
</script>
</body></html>
"""


def build_page(directory: Path) -> Path:
    """Write the throwaway page into `directory` and return its path."""
    import json

    page = directory / "pip-poses.html"
    page.write_text(
        PAGE.format(
            style=(STATIC / "style.css").as_uri(),
            script=(STATIC / "dashboard-pip.js").as_uri(),
            poses=json.dumps([list(pose) for pose in POSES]),
            bubble=json.dumps(SAMPLE_BUBBLE),
        ),
        encoding="utf-8",
    )
    return page


EMPTY_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="{style}"><link rel="stylesheet" href="{recordings}">
<style>.pip-art, .pip-eyes-open, .pip-eyes-closed, .pip-sleeping .pip-art {{ animation: none !important; }}
  body {{ display: block; padding: 28px; }} .rec-list {{ width: 340px; background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--radius); padding: 12px; }}</style></head>
<body><ul class="rec-list" id="rec-list"></ul>
<script src="{script}"></script>
<script>
  const recList = document.getElementById("rec-list");
  recList.innerHTML = '<li class="empty-state">No recordings yet.</li>';
  const host = document.createElement("div");
  host.className = "rec-empty-pip";
  recList.firstElementChild.append(host);
  mountPip(host, {{ pose: "sleeping", size: 72, bubble: {bubble} }});
</script></body></html>
"""


def empty_recordings_page(directory: Path) -> Path:
    """The empty Recordings list: a hand-kept copy of the markup `renderRecordingList` draws, with the real style
    sheets, Pip component and bubble text. `tests/test_pip.py` fails when the copy and the script drift apart."""
    import json

    text = (STATIC / "dashboard-recordings.js").read_text(encoding="utf-8")
    line = text.split('REC_EMPTY_PIP_LINE = "', 1)[1].split('"', 1)[0]
    page = directory / "pip-empty-recordings.html"
    page.write_text(
        EMPTY_PAGE.format(
            style=(STATIC / "style.css").as_uri(),
            recordings=(STATIC / "recordings.css").as_uri(),
            script=(STATIC / "dashboard-pip.js").as_uri(),
            bubble=json.dumps(line),
        ),
        encoding="utf-8",
    )
    return page


def run_empty(out: Path, browser: str | None, scale: float) -> Path:
    browser_path = _find_browser(browser)
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nexusvox-pip-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        target = work / out.name
        _photograph(browser_path, empty_recordings_page(work).as_uri(), target, work / "browser-profile", scale, 230)
        out.write_bytes(target.read_bytes())
    print(f"wrote {out}")
    return out


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
    run_empty(args.out.with_name(EMPTY_OUT.name), args.browser, args.scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
