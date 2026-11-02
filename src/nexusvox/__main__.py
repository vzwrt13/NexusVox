"""Entry point for `python -m nexusvox`."""

import logging
import sys
from pathlib import Path

from .app import NexusVoxApp
from .config import load_config
from .logsetup import setup_file_logging
from .paths import apply_models_dir, default_config_path, ensure_dirs
from .single_instance import SingleInstance, show_message


def main() -> None:
    # Before anything heavy: a second start must not build a second tray icon or hotkey hook.
    instance = SingleInstance()
    if not instance.acquire():
        if not instance.ring_running_instance():
            show_message("NexusVox is already running. Use its tray icon to open the dashboard.")
        sys.exit(0)

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

    app = NexusVoxApp(config)
    instance.watch(app.open_dashboard_from_second_start)

    try:
        app.run()
    except KeyboardInterrupt:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
