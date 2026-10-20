"""Entry point for `python -m nexusvox`."""

import logging
import sys

from .app import NexusVoxApp
from .config import load_config


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

    app = NexusVoxApp(config)

    try:
        app.run()
    except KeyboardInterrupt:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
