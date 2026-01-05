#!/usr/bin/env python3
"""Main entry point for Livestream List."""

import logging
import sys


def setup_logging() -> None:
    """Set up logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    """Main entry point."""
    setup_logging()

    try:
        from .gui.app import run

        return run()
    except ImportError as e:
        logging.error(f"Failed to import GUI: {e}")
        logging.error("Make sure PyGObject and libadwaita are installed:")
        logging.error("  sudo pacman -S python-gobject gtk4 libadwaita")
        return 1


if __name__ == "__main__":
    sys.exit(main())
