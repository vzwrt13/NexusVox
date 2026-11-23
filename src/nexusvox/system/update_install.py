r"""Install a newer release from inside the app: download the installer, check its signature, run it, quit.

Nothing happens before the user clicks *Install update* in the dashboard (the 1.0 promise: no network call the user
did not ask for). Then, in this order:

1. **Download** `NexusVox-Setup.exe` of the release the last *Check for updates* found, and only from there: the URL
   must be that asset of this repository (`update_check.is_installer_url`) and every redirect must stay on GitHub's
   release-asset hosts (`REDIRECT_HOSTS`), over https. The file goes to ``%LOCALAPPDATA%\NexusVox\updates`` as a
   ``.part`` file; its size must match what GitHub announced, and its SHA-256 too when GitHub sent a digest. Cancel
   and every failure delete it.
2. **Verify** the Authenticode signature (`authenticode.check_signature`): valid and trusted, timestamped, signed by
   `SIGNER`. Anything else deletes the file; it is never run.
3. **Run** it through a small hidden PowerShell waiter (only .NET calls, so no PowerShell module has to load, and
   none of PyInstaller's or the app's own environment variables): it waits for this process to end, with no time
   limit, because the installer refuses to run while NexusVox holds its mutex (`AppMutex` in
   `packaging/nexusvox.iss`) and the app's own files are in use, then runs the installer with ``/SILENT /SP-
   /NORESTART`` (a progress window, no wizard pages; per-user, no admin; the earlier folder and tasks are kept),
   deletes it and starts NexusVox again. Settings, history and recordings live outside the program folder and stay.
4. **Quit** the app the normal way (the tray's Quit), so a meeting is finalized and other apps' microphones are
   unmuted before the files are replaced.

Only the installed app offers this (`paths.is_frozen`): a run from source has no installed copy to replace.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Callable
from pathlib import Path

import httpx

from .authenticode import check_signature
from .paths import is_frozen, updates_dir
from .update_check import INSTALLER_NAME, installed_version, is_installer_url

logger = logging.getLogger(__name__)

SIGNER = "NightShift AI GmbH"
# Where github.com sends a release asset download. Nothing else is followed.
REDIRECT_HOSTS = frozenset(
    {"release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"}
)
MAX_REDIRECTS = 5
TIMEOUT_S = 20.0  # per phase (connect, each read); a stalled download fails after this long
CHUNK_BYTES = 1 << 16
# Silent, not very silent: the user sees the installer's progress window and any question it has to ask.
INSTALLER_ARGS = "/SILENT /SP- /NORESTART"
# Time for the page to read "installing" before the dashboard goes away with the app.
QUIT_DELAY_S = 1.5

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Variables the old process carries that must not reach the installer or the restarted app: PyInstaller's bootloader
# state (a frozen app started with it reads itself as a child of the old one) and what `paths.apply_models_dir` set,
# which the new version sets again for itself.
_INHERITED_PREFIXES = ("_PYI_", "_MEI")
_INHERITED_NAMES = {"PSModulePath", "HF_HUB_CACHE", "HF_XET_CACHE"}  # PSModulePath of PowerShell 7 breaks 5.1's

# The waiter. Paths and the process id come in through the environment, never spliced into the script. It waits for
# the app to end however long its shutdown takes (a long meeting is finalized first): a timeout would start the
# installer while the app still holds its mutex, and a silent install then fails without a word to the user. A process
# that is already gone throws in GetProcessById, which is the same as having waited.
_WAITER = (
    "$ErrorActionPreference = 'Continue';"
    "try { [System.Diagnostics.Process]::GetProcessById([int]$env:NEXUSVOX_UPDATE_PID).WaitForExit() } catch { };"
    "try { $p = [System.Diagnostics.Process]::Start("
    "[System.Diagnostics.ProcessStartInfo]::new($env:NEXUSVOX_UPDATE_SETUP, $env:NEXUSVOX_UPDATE_ARGS));"
    " $p.WaitForExit() } catch { };"
    "try { [System.IO.File]::Delete($env:NEXUSVOX_UPDATE_SETUP) } catch { };"
    "if ([System.IO.File]::Exists($env:NEXUSVOX_UPDATE_APP)) {"
    " [void][System.Diagnostics.Process]::Start($env:NEXUSVOX_UPDATE_APP) }"
)

OFFLINE_MESSAGE = "Could not reach GitHub. Check your internet connection and try again."


class UpdateError(Exception):
    """A step failed; `message` is the sentence the dashboard shows."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_allowed_hop(url: object) -> bool:
    """True for the installer URL itself and for an https address on GitHub's release-asset hosts."""
    if is_installer_url(url):
        return True
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and parts.hostname in REDIRECT_HOSTS and parts.port in (None, 443)


def download_installer(
    url: str,
    dest: Path,
    *,
    size: int,
    sha256: str | None = None,
    on_progress: Callable[[int], None] = lambda received: None,
    cancelled: threading.Event | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Download `url` to `dest`. Raises `UpdateError` (`refused`, `offline`, `server`, `size`, `digest`,
    `cancelled`); then no file is left behind."""
    if not is_installer_url(url):
        raise UpdateError("refused", "That is not the installer of a NexusVox release. Nothing was downloaded.")
    cancelled = cancelled or threading.Event()
    part = dest.with_name(dest.name + ".part")
    # The bytes counted and hashed must be the asset's own: no compressed transfer that httpx would decode.
    headers = {"User-Agent": f"NexusVox/{installed_version()}", "Accept-Encoding": "identity"}
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_S, follow_redirects=False, headers=headers) as client:
            for _ in range(MAX_REDIRECTS + 1):
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        location = urllib.parse.urljoin(url, response.headers.get("location", ""))
                        if not is_allowed_hop(location):
                            host = urllib.parse.urlsplit(location).hostname
                            logger.warning("Update download redirected to a refused host: %s", host)
                            raise UpdateError(
                                "refused",
                                "The download was sent to a server that is not GitHub's. Nothing was installed.",
                            )
                        url = location
                        continue
                    if response.status_code != 200:
                        logger.info("Update download refused: HTTP %d", response.status_code)
                        raise UpdateError(
                            "server",
                            f"GitHub could not deliver the installer (HTTP {response.status_code}). Try again later.",
                        )
                    _save(response, part, size, sha256, on_progress, cancelled)
                    os.replace(part, dest)
                    return
            raise UpdateError("refused", "The download was redirected too often. Nothing was installed.")
    except httpx.HTTPError as exc:
        logger.info("Update download failed: %s", exc.__class__.__name__)
        raise UpdateError("offline", OFFLINE_MESSAGE) from exc
    except OSError as exc:
        logger.error("Could not save the update to %s: %s", part, exc)
        raise UpdateError("disk", f"Could not save the installer: {exc.strerror or exc}") from exc
    finally:
        _discard(part)  # never raises: the error above is the one the user must see


def _discard(path: Path) -> bool:
    """Delete `path` if it exists; False (and a log line) when it cannot be, e.g. while antivirus still scans it."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not delete %s: %s", path, exc)
        return False
    return True


def _save(response: httpx.Response, part: Path, size: int, sha256: str | None, on_progress, cancelled) -> None:
    if response.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
        raise UpdateError("server", "GitHub sent the installer in an unexpected form. Try again later.")
    length = response.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) != size:
        raise UpdateError("size", "The installer on GitHub is not the size the release announced. Try again later.")
    part.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    received = 0
    with open(part, "wb") as file:
        for chunk in response.iter_bytes(CHUNK_BYTES):  # identity only (checked above): the bytes as stored
            if cancelled.is_set():
                raise UpdateError("cancelled", "Download cancelled. Nothing was installed.")
            received += len(chunk)
            if received > size:
                raise UpdateError("size", "The download was larger than the release announced. Nothing was installed.")
            file.write(chunk)
            digest.update(chunk)
            on_progress(received)
    if cancelled.is_set():
        raise UpdateError("cancelled", "Download cancelled. Nothing was installed.")
    if received != size:
        raise UpdateError("size", "The download stopped before the end. Check your connection and try again.")
    if sha256 and digest.hexdigest() != sha256:
        raise UpdateError("digest", "The download does not match the release's checksum. Nothing was installed.")


def verify_installer(path: Path) -> None:
    """Raises `UpdateError("signature")` and deletes `path` unless it is validly signed by `SIGNER`, timestamped."""
    signature = check_signature(path)
    if not signature.valid:
        reason = signature.reason or "the signature is not valid"
    elif SIGNER not in (signature.signer, signature.organisation):
        reason = f"it is signed by {signature.signer or 'an unknown publisher'}, not by {SIGNER}"
    elif not signature.timestamped:
        reason = "its signature has no timestamp"
    else:
        unchecked = "" if signature.revocation_checked else "; revocation not checked, its servers were unreachable"
        logger.info("Update installer signature verified: %s, timestamped%s", signature.signer, unchecked)
        return
    logger.warning("Update installer refused: %s", reason)
    what = "was deleted and not run" if _discard(path) else "was not run"
    raise UpdateError(
        "signature",
        f"The downloaded installer {what}: {reason}. You can download it yourself from the release page.",
    )


def _powershell() -> str:
    """Windows PowerShell by its full path, so no other `powershell.exe` on PATH can stand in."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def launch_installer(setup: Path, app_exe: Path, pid: int) -> None:
    """Start the hidden waiter that runs `setup` once process `pid` has ended, then starts `app_exe` again."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in _INHERITED_NAMES and not name.upper().startswith(_INHERITED_PREFIXES)
    }
    env.update(
        {
            # PyInstaller's documented switch for starting a new, independent instance of a frozen app.
            "PYINSTALLER_RESET_ENVIRONMENT": "1",
            "NEXUSVOX_UPDATE_SETUP": str(setup),
            "NEXUSVOX_UPDATE_APP": str(app_exe),
            "NEXUSVOX_UPDATE_PID": str(pid),
            "NEXUSVOX_UPDATE_ARGS": INSTALLER_ARGS,
        }
    )
    command = [_powershell(), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", _WAITER]
    flags = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
    for extra in (_CREATE_BREAKAWAY_FROM_JOB, 0):  # outlive a job that would end with this process, where allowed
        try:
            subprocess.Popen(
                command,
                env=env,
                cwd=str(setup.parent),  # never the program folder: the installer replaces what is in it
                creationflags=flags | extra,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            return
        except OSError as exc:
            if extra == 0:
                logger.error("Could not start the update installer: %s", exc)
                raise UpdateError("launch", "Could not start the installer. Nothing was changed.") from exc


class UpdateInstaller:
    """The *Install update* flow behind the dashboard: one at a time, its progress readable by polling `status`.

    States: `idle`, `downloading`, `verifying`, `installing` (the waiter runs, the app quits), `failed`, `cancelled`.
    """

    def __init__(
        self,
        on_quit: Callable[[], None] | None,
        *,
        folder: Callable[[], Path] = updates_dir,
        can_install: Callable[[], bool] = is_frozen,
        transport: httpx.BaseTransport | None = None,
        launcher: Callable[[Path, Path, int], None] = launch_installer,
        quit_delay_s: float = QUIT_DELAY_S,
    ) -> None:
        self._on_quit = on_quit
        self._folder = folder
        self._can_install = can_install
        self._transport = transport
        self._launcher = launcher
        self._quit_delay_s = quit_delay_s
        self._lock = threading.Lock()
        self._offer: dict | None = None  # {"version", "url", "size", "sha256"} from the last check
        self._state = "idle"
        self._message = ""
        self._received = 0
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def available(self) -> bool:
        """True where an update can be installed from here: the installed app, with a way to quit itself."""
        return self._on_quit is not None and self._can_install()

    def offer(self, check: dict) -> bool:
        """Remember the installer of a check's newer release; True when *Install update* may be offered for it."""
        installer = check.get("installer") if check.get("ok") and check.get("newer") else None
        with self._lock:
            if self._state in ("downloading", "verifying", "installing"):
                return False  # one is running: another tab must not offer a second (it keeps the running offer)
            self._offer = None
            if installer and self.available() and is_installer_url(installer.get("url")):
                self._offer = {**installer, "version": check.get("latest")}
            return self._offer is not None

    def status(self) -> dict:
        with self._lock:
            offer = self._offer or {}
            return {
                "state": self._state,
                "version": offer.get("version"),
                "received": self._received,
                "total": offer.get("size", 0),
                "message": self._message,
            }

    def start(self) -> dict:
        """Begin the download of the offered installer; `{"error", "message"}` when there is nothing to install."""
        with self._lock:
            if self._state in ("downloading", "verifying", "installing"):
                return {"error": "busy", "message": "The update is already being installed."}
            if not self.available():
                return {"error": "unavailable", "message": "Updates can only be installed in the installed app."}
            if self._offer is None:
                return {"error": "no_update", "message": "Check for updates first."}
            self._state, self._message, self._received = "downloading", "", 0
            self._cancel = threading.Event()
            offer = dict(self._offer)
            self._thread = threading.Thread(target=self._run, args=(offer, self._cancel), name="update-install")
            self._thread.daemon = True
            self._thread.start()
        return self.status()

    def cancel(self) -> dict:
        """Stop a running download; once the installer is verified and started there is nothing left to cancel."""
        with self._lock:
            if self._state == "downloading":
                self._cancel.set()
        return self.status()

    def _progress(self, received: int) -> None:
        with self._lock:
            self._received = received

    def _set(self, state: str, message: str = "") -> None:
        with self._lock:
            self._state, self._message = state, message

    def _run(self, offer: dict, cancel: threading.Event) -> None:
        """The worker thread. Whatever happens, it ends in `installing`, `failed` or `cancelled`: a state left at
        `downloading` or `verifying` would keep the page polling and refuse every later click as busy."""
        state, message = "failed", "Installing the update failed. Try again, or use the release page."
        dest = None
        try:
            folder = self._folder()
            dest = folder / INSTALLER_NAME
            state, message = self._install(offer, cancel, folder, dest)
        except UpdateError as exc:
            state, message = ("cancelled" if exc.code == "cancelled" else "failed"), exc.message
        except Exception:
            logger.exception("Installing the update failed")
        finally:
            if state != "installing" and dest is not None:
                _discard(dest)
            self._set(state, message)
        if state == "installing":
            logger.info("Update installer started; quitting so it can replace the program files")
            timer = threading.Timer(self._quit_delay_s, self._on_quit)  # type: ignore[arg-type]
            timer.daemon = True
            timer.start()

    def _install(self, offer: dict, cancel: threading.Event, folder: Path, dest: Path) -> tuple[str, str]:
        """Download, verify and launch; returns the final state. Raises `UpdateError` for a step that refuses."""
        _clear(folder)
        logger.info("Downloading the installer of version %s", offer.get("version"))
        download_installer(
            offer["url"],
            dest,
            size=offer["size"],
            sha256=offer.get("sha256"),
            on_progress=self._progress,
            cancelled=cancel,
            transport=self._transport,
        )
        self._set("verifying")
        verify_installer(dest)
        if cancel.is_set():  # a click that came in while the file was checked
            raise UpdateError("cancelled", "Download cancelled. Nothing was installed.")
        self._launcher(dest, Path(sys.executable), os.getpid())
        return "installing", "Installing the update. NexusVox closes now and starts again when it is done."


def _clear(folder: Path) -> None:
    """Remove what an earlier, interrupted attempt left in the updates folder."""
    if folder.is_dir():
        for leftover in folder.iterdir():
            if leftover.is_file():
                _discard(leftover)
