"""Entry point for `python -m nexusvox`."""

import logging
import sys
from pathlib import Path

from .app import NexusVoxApp
from .config import DEFAULT_CONFIG_PATH, load_config
from .logsetup import setup_file_logging


def main() -> None:
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
        config.source_path or DEFAULT_CONFIG_PATH.resolve(),
        Path(config.database.path).resolve(),
        config.cleanup.level,
        log_file or "(none, the log folder is not writable)",
    )

    app = NexusVoxApp(config)

    try:
        app.run()
    except KeyboardInterrupt:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
