"""Generate the dashboard screenshots for the README and the website from made-up demo data.

    python scripts/dashboard_screenshots.py [--out docs/images] [--browser PATH] [--scale 1.5]

The script starts the real dashboard against a temporary data folder that it fills with invented
dictations, meetings and uploads, and photographs the Analytics, Recordings and Settings tabs
with a headless Chromium browser (Microsoft Edge or Google Chrome, whichever is installed; no extra
package). The dashboard has one theme, dark, so there is one image per tab.

What stays out of the picture and out of reach:

* Your own data. The working directory moves into the temporary folder before anything is built, the
  config is a fresh in-memory one whose save path is inside that folder, and the folder is deleted at
  the end. `config.toml`, the database and the audio of your install are never opened.
* The machine. The probes that would show it (GPU, the language-model and translator servers, the audio
  devices, the log folder) are replaced by fixed answers, so nothing is probed, nothing opens an audio
  device, no hotkey listener starts, no model is loaded or downloaded and Docker is not touched.
* The network. The browser runs with a throwaway profile, no sync or update traffic, and every host
  except 127.0.0.1 unresolvable.

The images are written to a staging folder first and copied into --out only when all four succeeded, so a
failed run never leaves a mix of old and new images.

Run twice, the images differ only where the clock shows. The demo data is the same on every day (one fixed
seed per day of the window, the window ends today); the time zone and the browser language are pinned, so
the images do not depend on the machine either.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest import mock

# Pin the time zone of this process (the dashboard groups dictations by local day and hour, in SQLite) and of
# the browser, before anything reads the clock: the same images on every machine, in every season.
os.environ["TZ"] = "UTC0"
if hasattr(time, "tzset"):
    time.tzset()
BROWSER_LANGUAGE = "en-US"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

DEFAULT_OUT = _PROJECT_ROOT / "docs" / "images"
SEED = 20260101
VIEWPORT_WIDTH = 1280

# (dashboard tab, file name, CSS selector of the elements to hide or None, window height or None). A tab without
# a height is photographed in a viewport taller than the tab, and the empty strip below the last card is cut off,
# so an image ends at the content. Analytics and Settings run on for a long way (charts, more settings), so the
# page is served with the elements after the last card to show hidden: the cut sits at an element, not at a
# guess. The Recordings tab fills the window with its two panes, so it is photographed at a window's height.
VIEWPORT_HEIGHT = 4000
SHOTS = (
    ("analytics", "dashboard-analytics.png", ".chart-card:has(#chart-latency) ~ *", None),  # up to the latency chart
    ("recordings", "dashboard-recordings.png", None, 860),
    ("settings", "dashboard-settings.png", "#settings-card-vocabulary ~ *", None),  # Dictation and Vocabulary
)
CROP_PADDING = 32  # CSS pixels of page background kept below the last card

# ── Demo data ─────────────────────────────────────────────────────────────────────────────────────
# Invented text only: no real people, companies, products or places, and English only like the rest of the
# repository. The names are placeholders.

EN_SENTENCES = (
    "Thanks for the update, I will review the draft this afternoon and send my comments before the end of the day.",
    "Let's move the planning meeting to Thursday so that everyone has time to read the proposal first.",
    "Please add the new onboarding steps to the checklist and mark the old ones as obsolete.",
    "The quarterly report is almost ready, I only need the final numbers from the finance team.",
    "Could you check whether the export still works with the latest version before we publish the release notes?",
    "I think we should keep the first option, it is simpler to maintain and easier to explain to new colleagues.",
    "Reminder to self: book the conference room for the workshop and order lunch for twelve people.",
    "The customer asked for a short summary of the changes, so I will write three bullet points and add the diagram.",
    "Good morning everyone, here is the agenda for today: status updates, open questions and next steps.",
    "We fixed the login problem yesterday, the cause was an expired certificate on the test server.",
    "Please send me the slides by Friday, I want to rehearse the talk once over the weekend.",
    "That sounds great, let's schedule a short call next week to go through the details together.",
    "The prototype works on my machine, now I need to test it with a slower connection and a larger data set.",
    "Can you share the link to the shared folder again? I cannot find it in my bookmarks.",
    "I will be out of office from Monday until Wednesday, please contact the support team for anything urgent.",
    "Add a paragraph about data protection to the introduction and move the glossary to the end of the document.",
    "The meeting notes are in the shared document, please add your action items before tomorrow morning.",
    "Short question about the invoice: is the delivery date on it the one we agreed on in the contract?",
    "I rewrote the first section to make it shorter, the second one still needs a clearer example.",
    "Let me know if the new layout is easier to read, otherwise I will switch back to the previous version.",
    "Yes.",
    "Sounds good to me.",
    "I'll take a look and get back to you.",
    "Great, thank you very much.",
    "Please remind me to call the printer tomorrow at nine.",
    "The weather is nice today, so I am working from the terrace and will be online as usual.",
)

APPS = (
    ("outlook.exe", 30),
    ("code.exe", 22),
    ("chrome.exe", 18),
    ("slack.exe", 14),
    ("winword.exe", 9),
    ("notepad.exe", 4),
    ("teams.exe", 3),
)
MODELS = (("parakeet-tdt-0.6b", 88), ("whisper-large-v3-turbo", 12))
# Hour of the day (the time zone is pinned to UTC above), weighted towards office hours.
HOUR_WEIGHTS = (0, 0, 0, 0, 0, 0, 1, 3, 8, 12, 12, 10, 6, 8, 11, 11, 9, 6, 3, 2, 1, 1, 0, 0)
DAYS = 75

MEETINGS = (
    {
        "folder": "meeting-demo-planning",
        "title": "Weekly planning",
        "days_ago": 9,
        "minutes": 34,
        "turns": (
            (1, "Okay, let's start with the status of the release. Where do we stand?"),
            (2, "The last two bugs are fixed and the build has been green since this morning."),
            (3, "I still need to update the documentation for the export, but that is done by tomorrow."),
            (1, "Good. Then we can freeze on Thursday and publish on Friday if nothing else comes up."),
            (2, "Sounds fine to me. Should we tell the pilot users beforehand?"),
            (1, "Yes, please send them a short note today. Anything else on the list?"),
            (3, "One thing: the new setup guide needs a review by someone who has not seen it yet."),
            (1, "I'll ask a colleague to read it. Thanks everyone, see you on Thursday."),
        ),
    },
    {
        "folder": "meeting-demo-customer-call",
        "title": "Customer call: onboarding",
        "days_ago": 12,
        "minutes": 21,
        "turns": (
            (2, "Thank you for taking the time. We would like to understand how your team works today."),
            (1, "Of course. Right now everything starts with a shared mailbox and a spreadsheet."),
            (2, "And how many people use it every day?"),
            (1, "About eight. Two of them do most of the typing, the others only look things up."),
            (2, "That helps a lot. We will send you a proposal with two options by the end of the week."),
        ),
    },
    {
        "folder": "meeting-demo-retrospective",
        "title": "Retrospective",
        "days_ago": 16,
        "minutes": 47,
        "turns": (
            (1, "What went well this sprint? Let's collect a few points first."),
            (2, "The handover between the two teams was much smoother than last time."),
            (3, "Agreed. The daily notes helped, and the shorter meetings."),
            (1, "And what should we change?"),
            (3, "Reviews waited too long on Tuesday, we should agree on a maximum waiting time."),
            (2, "Maybe a fixed review slot in the afternoon would already be enough."),
            (1, "Let's try that for two weeks and look at the numbers again."),
        ),
    },
)
OWN_VOICE_LABEL = "Me"
SPEAKER_NAMES = {1: OWN_VOICE_LABEL, 2: "Alex Example", 3: "Sam Sample"}

# (file name, days ago, duration in ms, language, cleanup level, speaker turns or plain text)
UPLOADS = (
    ("podcast-episode-12.mp3", 1, 2_340_000, "en", "light", "turns"),
    ("voice-memo-ideas.wav", 2, 192_000, "en", None, "memo"),
    ("meeting-notes-voice.wav", 4, 305_000, "en", None, "notes"),
    ("interview-recording.mp3", 6, 1_485_000, "en", "medium", "interview"),
)
UPLOAD_TURNS = (
    (1, "Welcome to the show. Today we talk about how small teams keep their notes in order."),
    (2, "Thanks for having me. The short answer is: write things down once and find them again quickly."),
    (1, "That sounds simple. Where do most teams get stuck?"),
    (2, "Usually in the middle. The notes exist, but nobody knows where they ended up."),
)
INTERVIEW_TURNS = (
    (1, "Thanks for joining us. How did the project start?"),
    (2, "It began as a weekend experiment, and a few friends asked if they could use it too."),
    (1, "What was the hardest part of the first year?"),
    (2, "Saying no. There were many good ideas, and we could only build a few of them well."),
)
UPLOAD_TEXT_PLAIN = (
    "Ideas for the next workshop: start with a short demo, collect questions on a whiteboard, and finish with a "
    "list of three things everyone can try on their own the next day."
)
UPLOAD_TEXT_NOTES = (
    "Notes on the meeting: the schedule stays as planned, the documents go out by Wednesday, and we settle the "
    "open points afterwards with a short phone call."
)

DICTIONARY = (
    ("acme cloud", "Acme Cloud"),
    ("kube control", "kubectl"),
    ("sequel", "SQL"),
    ("jason", "JSON"),
    ("read me", "README"),
)
POLISH_PRESETS = (
    ("friendly", "Rewrite this in a friendly, relaxed tone without changing the meaning.", True),
    ("formal", "Rewrite this in a formal, polite register.", False),
    ("shorter", "Make this shorter. Keep every fact, drop everything else.", False),
)


def _weighted(rng: random.Random, pairs: tuple[tuple[str, int], ...]) -> str:
    return rng.choices([p[0] for p in pairs], weights=[p[1] for p in pairs])[0]


def _paragraphs(turns: tuple[tuple[int, str], ...], names: dict[int, str] | None = None) -> str:
    """The stored transcript text: "Speaker 1: ..." paragraphs, or the speakers' names where `names` has them."""
    names = names or {}
    return "\n\n".join(f"{names.get(speaker, f'Speaker {speaker}')}: {text}" for speaker, text in turns)


def _speaker_words(turns: tuple[tuple[int, str], ...]) -> str:
    """The `words` JSON of a transcript with speaker detection (what "Speakers per Upload" counts)."""
    words, clock = [], 0.0
    for speaker, text in turns:
        for word in text.split():
            words.append({"word": word, "start": round(clock, 2), "end": round(clock + 0.35, 2), "speaker": speaker})
            clock += 0.4
    return json.dumps(words)


def seed_database(db) -> None:
    """Fill the temporary database. Every day of the window has its own random stream, seeded by its position in
    the window, so the rows are the same on every day of the year and only their dates move."""
    from nexusvox.storage.models import FileTranscription, Transcription, utc_now

    # Noon of today (the time zone is UTC, see the top of this file). Dictations after noon today are left out:
    # a run later the same day gives the same rows.
    now = utc_now().replace(hour=12, minute=0, second=0, microsecond=0)
    rows = []
    for index in range(DAYS + 1):
        rng = random.Random(f"{SEED}-{index}")
        date = now - timedelta(days=DAYS - index)
        quiet = index % 7 in (5, 6)  # two quiet days a week, whichever weekday they fall on
        # A gentle upward trend over the window.
        base = 4 if quiet else 11
        count = max(0, round(base * (0.55 + 0.45 * index / DAYS) + rng.randint(-3, 3)))
        for _ in range(count):
            text = rng.choice(EN_SENTENCES)
            words = len(text.split())
            speaking_rate = rng.uniform(125, 185)  # words per minute while speaking
            duration_ms = int(words / speaking_rate * 60_000 + rng.randint(900, 2200))
            hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
            created = date.replace(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59), microsecond=0)
            confidence = round(rng.uniform(0.88, 0.995), 3)
            transcribe_ms = rng.randint(140, 420)
            model = _weighted(rng, MODELS)
            if model.startswith("whisper"):
                transcribe_ms += rng.randint(100, 250)
            cleaned = rng.random() < 0.35 and words > 6
            polished = rng.random() < 0.06 and words > 6
            reviewed = rng.random() < 0.10
            correct_a_word = rng.random() < 0.25 and words > 6
            word_to_replace = rng.randrange(words)
            latency_extra = rng.randint(60, 240)
            cleanup_ms = rng.randint(250, 700)
            polish_ms = rng.randint(600, 1800)
            app = _weighted(rng, APPS)
            if created > now:
                continue
            corrected = None
            if reviewed and correct_a_word:
                parts = text.split()
                parts[word_to_replace] = "the"
                corrected = " ".join(parts)
            rows.append(
                Transcription(
                    text=text,
                    language="en",
                    duration_ms=duration_ms,
                    created_at=created,
                    confidence=confidence,
                    model=model,
                    flagged=0,
                    corrected_text=corrected,
                    reviewed=int(reviewed),
                    transcribe_ms=transcribe_ms,
                    latency_ms=transcribe_ms + latency_extra + (cleanup_ms if cleaned else 0),
                    cleanup_status="applied" if cleaned else None,
                    cleanup_ms=cleanup_ms if cleaned else None,
                    app=app,
                    polish_status="applied" if polished else None,
                    polish_ms=polish_ms if polished else None,
                )
            )

    meeting_rows = []
    for index, meeting in enumerate(MEETINGS):
        meeting_rows.append(
            FileTranscription(
                text=_paragraphs(meeting["turns"], SPEAKER_NAMES),
                language="en",
                duration_ms=meeting["minutes"] * 60_000,
                created_at=now - timedelta(days=meeting["days_ago"], hours=index),
                model="parakeet-tdt-0.6b",
                original_filename=meeting["folder"],
                title=meeting["title"],
                confidence=0.96,
                kind="meeting",
                speaker_names=json.dumps({str(k): v for k, v in SPEAKER_NAMES.items()}),
                processing_ms=meeting["minutes"] * 700,
                words=_speaker_words(meeting["turns"]),
                # Shows the player; nothing plays, there is no file (the browser never asks for it in a still).
                audio_path=f"audio/meetings/{meeting['folder']}/meeting.wav",
            )
        )

    upload_rows = []
    for name, days_ago, duration_ms, language, cleanup, content in UPLOADS:
        turns = {"turns": UPLOAD_TURNS, "interview": INTERVIEW_TURNS}.get(content)
        text = _paragraphs(turns) if turns else {"memo": UPLOAD_TEXT_PLAIN, "notes": UPLOAD_TEXT_NOTES}[content]
        upload_rows.append(
            FileTranscription(
                text=text,
                language=language,
                duration_ms=duration_ms,
                created_at=now - timedelta(days=days_ago, hours=2),
                model="parakeet-tdt-0.6b",
                original_filename=name,
                confidence=0.95,
                words=_speaker_words(turns) if turns else None,
                cleaned_text=text if cleanup else None,
                cleanup_level=cleanup,
                kind="upload",
                processing_ms=duration_ms // 40,
            )
        )

    with db._session_factory() as session:
        session.add_all([*rows, *meeting_rows, *upload_rows])
        session.commit()
    for wrong, right in DICTIONARY:
        db.add_dictionary_entry(wrong, right)
    from nexusvox.storage.vocabulary import PolishPreset

    for name, instruction, is_default in POLISH_PRESETS:
        db.save_polish_preset(None, PolishPreset(name=name, instruction=instruction, is_default=is_default))


# ── Dashboard ─────────────────────────────────────────────────────────────────────────────────────


def _demo_patches() -> list[mock._patch]:
    """Fixed answers for everything the dashboard would otherwise ask the machine or the network.

    The config module's own `resolve_device` and `gpu_vram_gb` are replaced, which also decides what the
    functions that call them see (`cuda_available`, `llm_runs_fast`). A name that a dashboard module imported
    by itself (`from ..config import gpu_vram_gb`) is a separate binding and is replaced in that module too.
    """
    llm_models = [{"name": "gemma4:e4b", "size_gb": 9.6}]
    devices = {
        "inputs": ["Studio Microphone (Demo)", "Headset Microphone (Demo)"],
        "outputs": ["Speakers (Demo)", "Headset Earphones (Demo)"],
    }

    def pick_device(requested: str) -> str:
        return "cpu" if requested == "cpu" else "cuda"

    return [
        # A CUDA machine with 16 GB: the Settings tab then shows a GPU and the language-model card as usable.
        mock.patch("nexusvox.config.resolve_device", side_effect=pick_device),
        mock.patch("nexusvox.config.gpu_vram_gb", return_value=16.0),
        mock.patch("nexusvox.dashboard.api_models.resolve_device", side_effect=pick_device),
        mock.patch("nexusvox.dashboard.api_settings.gpu_vram_gb", return_value=16.0),
        # The model list never reads the model cache of this machine, whatever the device says.
        mock.patch("nexusvox.dashboard.api_models.model_download_state", return_value={"downloaded": None, "size": ""}),
        mock.patch("nexusvox.dashboard.api_settings.is_reachable", return_value=True),
        mock.patch("nexusvox.dashboard.api_settings.log_dir", return_value=Path("C:/Users/demo/NexusVox/logs")),
        mock.patch("nexusvox.dashboard.api_settings.llm_model_status", return_value={"state": "ok"}),
        mock.patch("nexusvox.dashboard.api_settings.llm_loaded_model", return_value=None),
        mock.patch("nexusvox.llm.installed_models", return_value=llm_models),
        mock.patch("nexusvox.dashboard.api_meetings.list_devices", return_value=devices),
    ]


STILL_CSS = "<style>*, *::before, *::after { transition: none !important; animation: none !important; }</style>"
CHART_SCRIPT = '<script src="chart.min.js"></script>'
STILL_CHARTS = "<script>Chart.defaults.animation = false;</script>"
_hide_css = ""  # the rule for the tab being photographed; set by `run` before each shot


def _tab_style(selector: str | None) -> str:
    return f"<style>{selector} {{ display: none !important; }}</style>" if selector else ""


def _serve_still(response):
    """Serve the page with chart animation and CSS transitions off, so a screenshot never catches the page
    mid-motion (two runs would then differ in a few pixels), and with the current tab's trailing elements hidden.
    Only this script's served copy of the page changes; the dashboard files do not."""
    if response.mimetype == "text/html" and response.status_code == 200:
        response.direct_passthrough = False  # a static file streams; read it into the response first
        html = response.get_data(as_text=True)
        html = html.replace(CHART_SCRIPT, CHART_SCRIPT + STILL_CHARTS, 1)
        html = html.replace("</head>", STILL_CSS + _hide_css + "</head>", 1)
        response.set_data(html)
        response.headers.pop("ETag", None)
    return response


def _check_page_markers() -> None:
    """Stop before any screenshot when the page no longer has the tag the animation switch hangs on: without it
    two runs would silently stop being identical."""
    index = Path(_PROJECT_ROOT / "src" / "nexusvox" / "dashboard" / "static" / "index.html")
    if CHART_SCRIPT not in index.read_text(encoding="utf-8"):
        raise SystemExit(
            f"index.html no longer contains {CHART_SCRIPT}: update CHART_SCRIPT in {Path(__file__).name} so that "
            "chart animation can still be switched off."
        )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _find_browser(explicit: str | None) -> Path:
    """The browser to use. A path or name that was asked for (--browser, NEXUSVOX_SCREENSHOT_BROWSER) must work:
    it is never swapped for another browser. Otherwise Edge, then Chrome, in their usual places."""
    asked = explicit or os.environ.get("NEXUSVOX_SCREENSHOT_BROWSER")
    if asked:
        found = Path(asked) if Path(asked).is_file() else shutil.which(asked)
        if not found:
            raise SystemExit(f"The browser {asked!r} is not a file and not on the PATH.")
        return Path(found).resolve()  # absolute: the working directory moves later
    candidates = []
    for root in filter(None, (os.environ.get(v) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"))):
        candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
        candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    candidates += [
        Path(found) for name in ("msedge", "chrome", "google-chrome", "chromium") if (found := shutil.which(name))
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise SystemExit("No Chromium browser found. Install Microsoft Edge or Google Chrome, or pass --browser PATH.")


def _photograph(browser: Path, url: str, target: Path, profile: Path, scale: float, height: int) -> None:
    """One headless screenshot of `url`. The launch URL redirects to the tab and sets the session cookie."""
    command = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-features=OptimizationHints,MediaRouter,Translate",
        f"--lang={BROWSER_LANGUAGE}",
        f"--accept-lang={BROWSER_LANGUAGE}",
        # Only the dashboard on 127.0.0.1 may be reached.
        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
        f"--user-data-dir={profile}",
        f"--window-size={VIEWPORT_WIDTH},{height}",
        f"--force-device-scale-factor={scale}",
        "--virtual-time-budget=10000",
        f"--screenshot={target}",
        url,
    ]
    env = {**os.environ, "TZ": "UTC"}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, env=env)
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"The browser did not finish within {exc.timeout:.0f} s for {target.name}.") from exc
    if result.returncode != 0 or not target.is_file():
        tail = "\n".join((result.stderr or "").strip().splitlines()[-15:])
        raise SystemExit(f"The browser failed for {target.name} (exit code {result.returncode}):\n{tail}")


def _crop_to_content(target: Path, scale: float) -> None:
    """Cut the empty strip below the last card. The page background is flat except for a glow in the top
    right corner, so a row is empty when every pixel of it, right of the sidebar, equals the bottom row's."""
    from PIL import Image, ImageChops

    with Image.open(target) as image:
        image = image.convert("RGB")
        width, height = image.size
        left = int(VIEWPORT_WIDTH * 0.35 * scale)  # right of the sidebar and its border
        strip = image.crop((left, 0, width, height))
        background = Image.new("RGB", strip.size, strip.getpixel((strip.width // 2, strip.height - 1)))
        box = ImageChops.difference(strip, background).point(lambda v: 255 if v > 6 else 0).getbbox()
        if box is None or box[3] >= height - 2:
            raise SystemExit(f"{target.name}: the content is longer than the {VIEWPORT_HEIGHT} px viewport.")
        bottom = min(height, box[3] + int(CROP_PADDING * scale))
        image.crop((0, 0, width, bottom)).save(target, optimize=True)


def run(out_dir: Path, browser: str | None, scale: float) -> list[Path]:
    global _hide_css
    browser_path = _find_browser(browser)
    _check_page_markers()
    out_dir = out_dir.resolve()  # before the working directory moves
    out_dir.mkdir(parents=True, exist_ok=True)

    # The browser's child processes can keep the profile folder locked for a moment after they exit (Windows).
    # That must not turn a run that wrote all its images into a failure.
    with tempfile.TemporaryDirectory(prefix="nexusvox-demo-", ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp)
        staging = data_dir / "shots"  # the images go to --out only when every one of them succeeded
        staging.mkdir()
        with contextlib.ExitStack() as stack:
            stack.callback(os.chdir, os.getcwd())
            os.chdir(data_dir)  # every relative path the app knows now lands in the throwaway folder
            for patch in _demo_patches():
                stack.enter_context(patch)

            from werkzeug.serving import make_server

            from nexusvox.config import Config
            from nexusvox.dashboard import _create_app
            from nexusvox.dashboard.api import DashboardAPI
            from nexusvox.extensions import ExtensionRegistry
            from nexusvox.storage.db import Database

            config = Config()
            config.source_path = data_dir / "config.toml"  # a save from the page goes here, nowhere else
            config.database.path = str(data_dir / "demo.db")
            config.database.audio_dir = "audio"
            config.hotkey.mode = "hold"
            config.llm.model = "gemma4:e4b"
            config.cleanup.level = "light"
            config.meeting.own_voice_label = OWN_VOICE_LABEL

            db = Database(config.database)
            stack.callback(db._engine.dispose)  # the open SQLite file would keep the folder from being deleted
            seed_database(db)
            extensions = ExtensionRegistry()
            extensions.add_builtin_exports()  # TXT, SRT and VTT, as in the app: no extension is loaded
            api = DashboardAPI(
                db._session_factory,
                config,
                db=db,
                benchmarks_dir=data_dir / "benchmarks",
                extensions=extensions,
            )

            port = _free_port()
            flask_app = _create_app(api, port)
            guard = flask_app.extensions["nexusvox_guard"]
            flask_app.after_request(_serve_still)
            server = make_server("127.0.0.1", port, flask_app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True, name="demo-dashboard")
            thread.start()
            stack.callback(server.server_close)
            stack.callback(thread.join, 5)
            stack.callback(server.shutdown)

            for tab, filename, hide, height in SHOTS:
                _hide_css = _tab_style(hide)
                url = f"http://127.0.0.1:{port}/?launch={guard.new_launch_secret()}&tab={tab}"
                profile = data_dir / "browser-profile" / tab  # a fresh profile each time: no cookie carries over
                target = staging / filename
                _photograph(browser_path, url, target, profile, scale, height or VIEWPORT_HEIGHT)
                if height is None:
                    _crop_to_content(target, scale)

        written = []
        for _tab, filename, _hide, _height in SHOTS:
            destination = out_dir / filename
            shutil.copyfile(staging / filename, destination)
            written.append(destination)
            print(f"wrote {destination}")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output folder (default: {DEFAULT_OUT})")
    parser.add_argument("--browser", help="path or name of msedge or chrome (default: found automatically)")
    parser.add_argument("--scale", type=float, default=1.5, help="device pixel ratio of the images (default: 1.5)")
    args = parser.parse_args(argv)
    run(args.out, args.browser, args.scale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
