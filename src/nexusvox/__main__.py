"""Entry point for `python -m nexusvox`."""

import logging
import subprocess
import sys
import threading
from pathlib import Path

from .app.core import NexusVoxApp
from .config import load_config
from .system.logsetup import set_file_log_level, setup_file_logging
from .system.paths import apply_models_dir, default_config_path, ensure_dirs
from .system.single_instance import SingleInstance, show_message

NOT_A_CHECKOUT = "(not a git checkout)"


def _is_checkout(folder: Path) -> bool:
    """True when *folder* is `<checkout>/src/nexusvox` of a git checkout, and the app is not a frozen build.

    An installed copy that sits inside some other repository (a virtual environment in a project) is not one.
    """
    return not getattr(sys, "frozen", False) and (folder.parent.parent / ".git").exists()


def _git_revision(folder: Path) -> str:
    """`branch@commit` of the checkout in *folder* (`_is_checkout` is the caller's business)."""

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", *args],
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return done.stdout.strip()

    try:
        return f"{git('rev-parse', '--abbrev-ref', 'HEAD')}@{git('rev-parse', '--short', 'HEAD')}"
    except (OSError, subprocess.SubprocessError):
        return NOT_A_CHECKOUT


def _log_code_revision() -> None:
    """Logs which code runs. The git calls can take seconds, so they never delay the start: a checkout logs from a
    background thread, anything else (an installed copy, a frozen build) at once without asking git."""
    folder = Path(__file__).resolve().parent
    log = logging.getLogger(__name__).info
    if _is_checkout(folder):
        threading.Thread(
            target=lambda: log("Code: %s, %s", folder, _git_revision(folder)), name="git-revision", daemon=True
        ).start()
    else:
        log("Code: %s, %s", folder, NOT_A_CHECKOUT)


AUTOSTART_FLAG = "--autostart"


def is_autostart(argv: list[str]) -> bool:
    """True when the Windows autostart entry started us: the app then stays in the tray without a window."""
    return AUTOSTART_FLAG in argv


def main() -> None:
    # Before anything heavy: a second start must not build a second tray icon or hotkey hook.
    instance = SingleInstance()
    if not instance.acquire():
        if not instance.ring_running_instance():
            show_message("NexusVox is already running. Use its tray icon to open the dashboard.")
        sys.exit(0)

    try:
        ensure_dirs()
        apply_models_dir()
        # Logging first, so what `load_config` has to say (a config moved aside, a model that is gone) reaches the
        # file too: the windowed app has no console. The level and the full-text switch are in the config, which is
        # not read yet; they are applied right after it.
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        loaded: list = []
        log_file = setup_file_logging(lambda: bool(loaded) and loaded[0].log_full_text)
        config = load_config()
        loaded.append(config)
        level = getattr(logging, config.log_level)
        # DEBUG is for NexusVox's own loggers: comtypes and urllib3 log every COM call and request at DEBUG
        # and would bury the lines the user turned it on to read.
        if level > logging.INFO:
            logging.getLogger().setLevel(level)
        logging.getLogger("nexusvox").setLevel(level)
        set_file_log_level(level)

        # Which config and database this process runs on: a config.toml in the wrong working directory is
        # otherwise only noticed as a setting that does nothing.
        logging.getLogger(__name__).info(
            "Config: %s, database: %s, cleanup: %s, log file: %s",
            config.source_path or default_config_path().resolve(),
            Path(config.database.path).resolve(),
            config.cleanup.level,
            log_file or "(none, the log folder is not writable)",
        )

        # Which code runs: a retest against the wrong checkout is otherwise only noticed as a fix that does nothing.
        _log_code_revision()

        app = NexusVoxApp(config, autostart=is_autostart(sys.argv[1:]))
        instance.watch(app.open_dashboard_from_second_start)

        try:
            app.run()
        except KeyboardInterrupt:
            pass
    finally:
        instance.release()  # a restart while we shut down must find the mutex free

    sys.exit(0)


if __name__ == "__main__":
    main()
