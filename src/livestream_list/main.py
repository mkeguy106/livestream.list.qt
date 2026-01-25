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

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def main() -> int:
    """Main entry point."""
    setup_logging()

    try:
        from .gui.app import run

        return run()
    except ImportError as e:
        logging.error(f"Failed to import GUI: {e}")
        logging.error("Make sure PySide6 is installed:")
        logging.error("  pip install PySide6")
        return 1


if __name__ == "__main__":
    sys.exit(main())
