"""Self-test that runs INSIDE the frozen app:  NexusVox.exe --selftest <work-dir> [--download] [--only <steps>]

A windowed build has no console, so the result goes to `<work-dir>/selftest.json` and the process exit code
(0 = every check passed). The test never touches the user's own config, database or model cache: everything
lives under <work-dir>, the Hugging Face cache included (<work-dir>/hf). With --download the models are fetched
from Hugging Face into it (a real first-run download, ~780 MiB); without it the test runs offline and the model
steps fail unless an earlier run already filled <work-dir>/hf.

It covers what the tray menu and the hotkey cannot show to a script: the system tray icon starts, the
dashboard serves its pages and API from the bundled files, extensions found next to the exe load, and the
CPU Parakeet model loads and transcribes a spoken sentence.

`--only decode,whisper` runs just those steps after the setup (the names passed to `guarded` below). `--only decode`
checks the bundled FFmpeg (LGPL, no GPL parts, decodes every upload format) without a tray icon, the dashboard
port, a window or a download.
"""

from __future__ import annotations

import asyncio
import faulthandler
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
import wave
from pathlib import Path
from typing import NoReturn

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


def _tone_frames(seconds: int, rate: int = 48_000) -> list:
    """One frame per second of a 440 Hz tone, s16 mono. PyAV converts them to what each encoder takes."""
    import av
    import numpy as np

    tone = (np.sin(2 * np.pi * 440 * np.arange(rate) / rate) * 8000).astype(np.int16).reshape(1, -1)
    frames = []
    for second in range(seconds):
        frame = av.AudioFrame.from_ndarray(tone, format="s16", layout="mono")
        frame.sample_rate = rate
        frame.pts = second * rate
        frames.append(frame)
    return frames


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


def run(work: Path, download: bool, only: set[str] | None = None) -> NoReturn:
    def crumb(text: str) -> None:
        with open(work / "progress.txt", "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%H:%M:%S')} {text}\n")

    checks: dict[str, str] = {}

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks[name] = ("PASS " if ok else "FAIL ") + detail

    work.mkdir(parents=True, exist_ok=True)
    hang_file = open(work / "hang.txt", "w")  # noqa: SIM115 - stays open for the whole run

    def guarded(name: str, func, timeout: float = 120) -> None:
        if only is not None and name != "setup" and name not in only:
            return
        crumb(f"step {name}")
        started = time.perf_counter()
        # A step that runs past its budget must leave a trace (a windowed build has no console to Ctrl+C in),
        # but a healthy long step must not fill the file: the timer is armed per step and cancelled after.
        faulthandler.dump_traceback_later(timeout, file=hang_file)
        try:
            func()
        except Exception as exc:  # the test reports a failing step and goes on to the next
            record(name, False, f"{type(exc).__name__}: {exc}\n" + "".join(traceback.format_tb(exc.__traceback__)[-4:]))
        finally:
            faulthandler.cancel_dump_traceback_later()
        checks[f"({name} took)"] = f"INFO {time.perf_counter() - started:.1f} s"

    # Never touch the user's real Hugging Face cache; without --download nothing may reach the network.
    os.environ["HF_HOME"] = str(work / "hf")
    os.environ["HF_HUB_CACHE"] = str(work / "hf" / "hub")
    if not download:
        os.environ["HF_HUB_OFFLINE"] = "1"
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
        from nexusvox.storage.db import Database

        state["config"] = load_config(work / "config.toml")
        state["config"].database.path = str(work / "nexusvox.db")
        state["db"] = Database(state["config"].database)
        record("setup", True)

    guarded("setup", setup)
    if "config" not in state:
        finish(work, checks)
    config, db = state["config"], state["db"]

    def decode() -> None:
        # The bundled FFmpeg must be the LGPL build (packaging/lgpl_av.py) and still decode every upload format.
        import av
        import av._core

        from nexusvox.transcription.file_transcribe import convert_to_wav

        meta = av._core.library_meta
        licences = {name: info["license"] for name, info in meta.items()}
        record("FFmpeg is LGPL", all("LGPL" in value for value in licences.values()), str(set(licences.values())))
        configure = meta["libavcodec"]["configuration"].split()
        forbidden = ("--enable-gpl", "--enable-nonfree", "--enable-libx264", "--enable-libx265")
        gpl = [switch for switch in configure if switch in forbidden]
        record("FFmpeg has no GPL parts", not gpl, " ".join(gpl))
        # suffix -> (container format, encoder); the formats the upload dialog takes, plus m4a (AAC)
        formats = {
            ".wav": ("wav", "pcm_s16le"),
            ".mp3": ("mp3", "libmp3lame"),
            ".flac": ("flac", "flac"),
            ".ogg": ("ogg", "libvorbis"),
            ".m4a": ("ipod", "aac"),
            ".webm": ("webm", "libopus"),
        }
        for suffix, (container_format, codec) in formats.items():
            path = work / f"tone{suffix}"
            with av.open(str(path), "w", format=container_format) as container:
                stream = container.add_stream(codec, rate=48_000)
                stream.layout = "mono"
                for frame in _tone_frames(seconds=2):
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode(None):
                    container.mux(packet)
            with convert_to_wav(path) as chunks:
                duration_ms = chunks.duration_ms
            record(f"decodes {suffix}", 1800 <= duration_ms <= 2300, f"{duration_ms} ms")

    guarded("decode", decode)

    def speech() -> None:
        _speak(work / "speech.wav")
        record("speech sample rendered", (work / "speech.wav").stat().st_size > 32000)

    guarded("speech", speech)

    def pcm_of_speech() -> bytes:
        with wave.open(str(work / "speech.wav")) as handle:
            return handle.readframes(handle.getnframes())

    def tray() -> None:
        from nexusvox.system.tray import SystemTray

        icon = SystemTray(
            on_quit=lambda: None,
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

        # The dashboard refuses a client without its session cookie; trade a launch secret for it, the way
        # the browser does when the tray opens the dashboard.
        import nexusvox.dashboard as dashboard_module
        from nexusvox.dashboard.scripts import page_scripts

        if dashboard_module._guard is None:
            raise RuntimeError("no dashboard port is free (another NexusVox running?); close it and run again")
        base = f"http://127.0.0.1:{dashboard_module._port}"
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        secret = dashboard_module._guard.new_launch_secret()
        opener.open(f"{base}/?launch={secret}", timeout=15).read()

        def get(path: str) -> tuple[int, bytes]:
            with opener.open(base + path, timeout=15) as response:
                return response.status, response.read()

        bodies: dict[str, bytes] = {}
        for path in (
            "/",
            "/style.css",
            "/Geist-Variable.woff2",
            "/api/models/status",
            "/api/extensions",
        ):
            status, body = get(path)
            bodies[path] = body
            record(f"dashboard GET {path}", status == 200 and len(body) > 0, f"{status}, {len(body)} bytes")
        # The scripts: whatever index.html loads has to be in the build.
        scripts = page_scripts(bodies["/"].decode())
        record("index.html loads the scripts", {"chart.min.js", "dashboard-core.js"} <= set(scripts), str(scripts))
        for script in scripts:
            status, body = get(f"/{script}")
            record(f"dashboard GET /{script}", status == 200 and len(body) > 0, f"{status}, {len(body)} bytes")
        body = bodies["/api/extensions"]
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

    guarded("parakeet", parakeet, timeout=1800)

    # One guarded step per native library: a machine without an output device must not hide the others.
    def lingua() -> None:
        from nexusvox.dictation.lang_detect import detect_language

        record("lingua detects German", detect_language("Das ist ein kurzer deutscher Satz zum Testen.") == "de")

    def portaudio() -> None:
        import sounddevice

        record("sounddevice sees PortAudio", len(sounddevice.query_devices()) > 0, sounddevice.__version__)

    def wasapi_sessions() -> None:
        from nexusvox.audio.mic_guard import enumerate_capture_sessions

        record("mic guard enumerates WASAPI sessions", isinstance(enumerate_capture_sessions(), list))

    def loopback() -> None:
        import pyaudiowpatch

        pa = pyaudiowpatch.PyAudio()
        try:
            device = pa.get_default_wasapi_loopback()
        finally:
            pa.terminate()
        record("WASAPI loopback device found", bool(device), str(device.get("name")))

    def hook() -> None:
        from pynput import keyboard

        listener = keyboard.Listener(on_press=lambda key: None, win32_event_filter=lambda m, d: True)
        listener.start()
        time.sleep(1)
        alive = listener.is_alive()
        listener.stop()
        record("pynput low-level hook starts", alive)

    for step_name, step in (
        ("lingua", lingua),
        ("sounddevice", portaudio),
        ("mic guard", wasapi_sessions),
        ("WASAPI loopback", loopback),
        ("pynput", hook),
    ):
        guarded(step_name, step)

    def overlay() -> None:
        import tkinter

        root = tkinter.Tk()  # the dictation overlay is a Tk window
        root.withdraw()
        root.destroy()
        record("tkinter creates a root window", True)

    guarded("overlay", overlay)

    def whisper() -> None:
        # The shipped Whisper is ~1.6 GB; the smoke test only needs the engine, so it borrows the registry
        # entry for the 150 MB base model that used to ship.
        from nexusvox.config import MODEL_REGISTRY
        from nexusvox.transcribers.local_whisper import LocalWhisperTranscriber

        MODEL_REGISTRY["whisper-base"] = {
            **MODEL_REGISTRY["whisper-large-v3-turbo"],
            "hf_name": "Systran/faster-whisper-base",
        }
        config.inference.model = "whisper-base"
        pcm = pcm_of_speech()

        async def go():
            transcriber = LocalWhisperTranscriber(config.inference, "cpu", "int8")
            await transcriber.connect()
            await transcriber.send_audio_chunk(pcm)
            return await transcriber.finish(timeout=300)

        result = asyncio.run(go())
        record("Whisper (ctranslate2) transcribes", "hello world" in result.text.lower(), repr(result.text))

    guarded("whisper", whisper, timeout=300)

    finish(work, checks)


def finish(work: Path, checks: dict[str, str]) -> NoReturn:
    failed = [name for name, value in checks.items() if value.startswith("FAIL")]
    (work / "selftest.json").write_text(json.dumps({"failed": failed, "checks": checks}, indent=2), encoding="utf-8")
    os._exit(1 if failed else 0)  # tray and server threads are daemons, but make sure nothing lingers


def leave_note(name: str, text: str) -> None:
    """No console to print to: leave a message where a person would look. The working directory may be
    read-only (Program Files), so fall back to the temp folder; never let this raise."""
    for folder in (Path.cwd(), Path(tempfile.gettempdir())):
        try:
            (folder / name).write_text(text, encoding="utf-8")
            return
        except OSError:
            continue


def main(argv: list[str]) -> NoReturn:
    position = argv.index("--selftest") + 1
    if position >= len(argv) or argv[position].startswith("--"):
        leave_note("selftest-usage.txt", "usage: NexusVox.exe --selftest <work-dir> [--download] [--only <steps>]\n")
        sys.exit(2)
    only = None
    if "--only" in argv:
        steps = argv.index("--only") + 1
        only = set(argv[steps].split(",")) if steps < len(argv) else set()
    run(Path(argv[position]), "--download" in argv, only)
