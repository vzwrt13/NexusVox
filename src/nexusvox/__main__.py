"""Entry point for `python -m nexusvox`."""

import logging
import subprocess
import sys
from pathlib import Path

from .app import NexusVoxApp
from .config import load_config
from .logsetup import setup_file_logging
from .paths import apply_models_dir, default_config_path, ensure_dirs


def _git_revision() -> str:
    """`branch@commit` of the checkout this code runs from, "(not a git checkout)" for an installed copy."""
    folder = Path(__file__).resolve().parent

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
        return "(not a git checkout)"


def main() -> None:
    ensure_dirs()
    apply_models_dir()
    config = load_config()
    level = getattr(logging, config.log_level)
    # DEBUG is for NexusVox's own loggers: comtypes and urllib3 log every COM call and request at DEBUG
    # and would bury the lines the user turned it on to read.
    logging.basicConfig(
        level=max(level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("nexusvox").setLevel(level)
    log_file = setup_file_logging(lambda: config.log_full_text, level=level)

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
    logging.getLogger(__name__).info("Code: %s, %s", Path(__file__).resolve().parent, _git_revision())

    app = NexusVoxApp(config)

    try:
        app.run()
    except KeyboardInterrupt:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
