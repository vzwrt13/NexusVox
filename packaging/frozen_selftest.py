"""Self-test that runs INSIDE the frozen app:  NexusVox.exe --selftest <work-dir> [--download]

A windowed build has no console, so the result goes to `<work-dir>/selftest.json` and the process exit code
(0 = every check passed). The test never touches the user's own config, database or model cache: everything
lives under <work-dir>. With --download the Parakeet model is fetched from Hugging Face into
<work-dir>/hf (a real first-run download); without it the model must already be in the default cache.

It covers what the tray menu and the hotkey cannot show to a script: the system tray icon starts, the
dashboard serves its pages and API from the bundled files, extensions found next to the exe load, and the
CPU Parakeet model loads and transcribes a spoken sentence.
"""

from __future__ import annotations

import asyncio
import faulthandler
import json
import os
import subprocess
import time
import traceback
import urllib.request
import wave
from pathlib import Path

SENTENCE = "Hello world. This is a test of the frozen application."


def _speak(path: Path) -> None:
    """Render SENTENCE to a 16 kHz mono PCM16 WAV with the Windows speech synthesiser."""
    target = str(path).replace("'", "''")  # a quote in the folder name must not end the PowerShell string
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono');"
        f"$s.SetOutputToWaveFile('{target}', $f); $s.Speak('{SENTENCE}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, creationflags=0x08000000)


def _fake_pro(folder: Path) -> None:
    """A stand-in for the Pro add-on: a package with an entry point and one dashboard panel."""
    package = folder / "fake_pro"
    (package / "static").mkdir(parents=True, exist_ok=True)
    (package / "static" / "panel.js").write_text("NexusVoxExt.registerPanel('fake-pro', c => {});\n")
    (package / "__init__.py").write_text(
        "from pathlib import Path\n"
        "from flask import Blueprint, jsonify\n"
        "from nexusvox.extensions import DashboardPanel, Extension\n\n"
        "bp = Blueprint('fake_pro', __name__)\n\n\n"
        "@bp.route('/ping')\n"
        "def ping():\n    return jsonify({'pong': True})\n\n\n"
        "class FakePro(Extension):\n"
        "    name = 'fake-pro'\n    version = '1.0'\n    api_version = 1\n\n"
        "    def dashboard_panels(self):\n"
        "        return [DashboardPanel(id='fake-pro', title='Fake Pro', "
        "static_dir=Path(__file__).parent / 'static', blueprint=bp)]\n"
    )
    info = folder / "fake_pro-1.0.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: fake-pro\nVersion: 1.0\n")
    (info / "entry_points.txt").write_text("[nexusvox.extensions]\nfake_pro = fake_pro:FakePro\n")


def run(work: Path, download: bool) -> int:
    def crumb(text: str) -> None:
        with open(work / "progress.txt", "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%H:%M:%S')} {text}\n")

    checks: dict[str, str] = {}

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks[name] = ("PASS " if ok else "FAIL ") + detail

    def guarded(name: str, func) -> None:
        crumb(f"step {name}")
        started = time.perf_counter()
        try:
            func()
        except Exception as exc:  # the test reports a failing step and goes on to the next
            record(name, False, f"{type(exc).__name__}: {exc}\n" + "".join(traceback.format_tb(exc.__traceback__)[-4:]))
        checks[f"({name} took)"] = f"INFO {time.perf_counter() - started:.1f} s"

    work.mkdir(parents=True, exist_ok=True)
    # A hung step must leave a trace: a windowed build has no console to Ctrl+C in.
    faulthandler.dump_traceback_later(90, repeat=True, file=open(work / "hang.txt", "w"))  # noqa: SIM115
    if download:
        os.environ["HF_HOME"] = str(work / "hf")
        os.environ["HF_HUB_CACHE"] = str(work / "hf" / "hub")
    crumb("start")
    state: dict = {}

    def setup() -> None:
        os.chdir(work)
        ext = work / "extensions"
        _fake_pro(ext)
        # The launcher already added the folder next to the exe; this exercises the same function for the
        # self-test's own folder.
        from extension_folders import add_extension_folders

        add_extension_folders((str(ext),))
        (work / "config.toml").write_text(
            '[inference]\ndevice = "cpu"\nmodel = "parakeet-tdt-0.6b"\n',
            encoding="utf-8",
        )
        from nexusvox.config import load_config
        from nexusvox.db import Database

        state["config"] = load_config(work / "config.toml")
        state["config"].database.path = str(work / "nexusvox.db")
        state["db"] = Database(state["config"].database)
        record("setup", True)

    guarded("setup", setup)
    if "config" not in state:
        return finish(work, checks)
    config, db = state["config"], state["db"]

    def speech() -> None:
        _speak(work / "speech.wav")
        record("speech sample rendered", (work / "speech.wav").stat().st_size > 32000)

    guarded("speech", speech)

    def pcm_of_speech() -> bytes:
        with wave.open(str(work / "speech.wav")) as handle:
            return handle.readframes(handle.getnframes())

    def tray() -> None:
        from nexusvox.tray import SystemTray

        icon = SystemTray(
            on_quit=lambda: None,
            on_toggle_language=lambda: None,
            get_language=lambda: "en",
            on_open_dashboard=lambda: None,
        )
        icon.start()
        time.sleep(3)
        alive = icon._thread is not None and icon._thread.is_alive()
        icon.stop()
        record("tray icon runs", alive)

    guarded("tray icon runs", tray)

    def dashboard() -> None:
        from nexusvox.dashboard import ensure_dashboard_api

        ensure_dashboard_api(db, config, None)
        base = "http://127.0.0.1:47392"

        # The dashboard refuses a client without its session cookie; trade a launch secret for it, the way
        # the browser does when the tray opens the dashboard.
        import nexusvox.dashboard as dashboard_module

        if dashboard_module._guard is None:
            raise RuntimeError("port 47392 is taken (another NexusVox running?); close it and run again")
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        secret = dashboard_module._guard.new_launch_secret()
        opener.open(f"{base}/?launch={secret}", timeout=15).read()

        def get(path: str) -> tuple[int, bytes]:
            with opener.open(base + path, timeout=15) as response:
                return response.status, response.read()

        for path in (
            "/",
            "/dashboard.js",
            "/style.css",
            "/chart.min.js",
            "/Geist-Variable.woff2",
            "/api/models/status",
            "/api/extensions",
        ):
            status, body = get(path)
            record(f"dashboard GET {path}", status == 200 and len(body) > 0, f"{status}, {len(body)} bytes")
        _, body = get("/api/extensions")
        record("extension found next to the exe", "fake-pro" in body.decode(), body.decode()[:120])
        status, body = get("/ext/fake-pro/panel.js")
        record("extension panel script served", status == 200, f"{status}")
        status, body = get("/api/ext/fake-pro/ping")
        record("extension panel API served", status == 200 and b"pong" in body, body.decode()[:60])

    guarded("dashboard", dashboard)

    def parakeet() -> None:
        from nexusvox.transcribers.local_parakeet import LocalParakeetTranscriber

        pcm = pcm_of_speech()

        async def go():
            transcriber = LocalParakeetTranscriber(config.inference)
            await transcriber.connect()
            seen: list[str] = []
            started = time.perf_counter()
            while time.perf_counter() - started < 1800:
                state = LocalParakeetTranscriber.load_status()["state"]
                if not seen or not seen[-1].startswith(f"{state}@"):
                    seen.append(f"{state}@{time.perf_counter() - started:.0f}s")
                if state in ("ready", "error"):
                    break
                await asyncio.sleep(0.5)
            checks["model load states"] = "INFO " + " -> ".join(seen)
            status = LocalParakeetTranscriber.load_status()
            if status["state"] != "ready":
                raise RuntimeError(f"model did not load: {status}")
            await transcriber.send_audio_chunk(pcm)
            return await transcriber.finish(timeout=120)

        result = asyncio.run(go())
        text = result.text.lower()
        record("Parakeet transcribes", "hello world" in text and "frozen" in text, repr(result.text))

    guarded("parakeet", parakeet)

    def native() -> None:
        from nexusvox.lang_detect import detect_language

        record("lingua detects German", detect_language("Das ist ein kurzer deutscher Satz zum Testen.") == "de")
        import sounddevice

        record("sounddevice sees PortAudio", len(sounddevice.query_devices()) > 0, sounddevice.__version__)
        from nexusvox.mic_guard import enumerate_capture_sessions

        record("mic guard enumerates WASAPI sessions", isinstance(enumerate_capture_sessions(), list))
        import pyaudiowpatch

        pa = pyaudiowpatch.PyAudio()
        loopback = pa.get_default_wasapi_loopback()
        pa.terminate()
        record("WASAPI loopback device found", bool(loopback), str(loopback.get("name")))
        from pynput import keyboard

        listener = keyboard.Listener(on_press=lambda key: None, win32_event_filter=lambda m, d: True)
        listener.start()
        time.sleep(1)
        alive = listener.is_alive()
        listener.stop()
        record("pynput low-level hook starts", alive)

    guarded("native", native)

    def overlay() -> None:
        import tkinter

        root = tkinter.Tk()  # the dictation overlay is a Tk window
        root.withdraw()
        root.destroy()
        record("tkinter creates a root window", True)

    guarded("overlay", overlay)

    def whisper() -> None:
        from nexusvox.transcribers.local_whisper import LocalWhisperTranscriber

        config.inference.model = "whisper-base"
        pcm = pcm_of_speech()

        async def go():
            transcriber = LocalWhisperTranscriber(config.inference, "cpu", "int8", language="en")
            await transcriber.connect()
            await transcriber.send_audio_chunk(pcm)
            return await transcriber.finish(timeout=300)

        result = asyncio.run(go())
        record("Whisper (ctranslate2) transcribes", "hello world" in result.text.lower(), repr(result.text))

    guarded("whisper", whisper)

    return finish(work, checks)


def finish(work: Path, checks: dict[str, str]) -> int:
    failed = [name for name, value in checks.items() if value.startswith("FAIL")]
    (work / "selftest.json").write_text(json.dumps({"failed": failed, "checks": checks}, indent=2), encoding="utf-8")
    os._exit(1 if failed else 0)  # tray and server threads are daemons, but make sure nothing lingers


def main(argv: list[str]) -> int:
    position = argv.index("--selftest") + 1
    if position >= len(argv) or argv[position].startswith("--"):
        # No console to print to: leave the usage where a person would look.
        Path("selftest-usage.txt").write_text("usage: NexusVox.exe --selftest <work-dir> [--download]\n")
        return 2
    return run(Path(argv[position]), "--download" in argv)
