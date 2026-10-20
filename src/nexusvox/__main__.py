"""Entry point for `python -m nexusvox`."""

import logging
import sys

from .app import NexusVoxApp
from .config import load_config


def main() -> None:
    config = load_config()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app = NexusVoxApp(config)

    try:
        app.run()
    except KeyboardInterrupt:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
