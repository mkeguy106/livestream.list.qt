#!/usr/bin/env python3
"""Main entry point for Livestream List."""

import faulthandler
import logging
import sys

# Print Python traceback on SIGSEGV/SIGFPE/SIGABRT instead of silently crashing
# sys.stderr is None in PyInstaller --windowed builds (no console)
if sys.stderr is not None:
    faulthandler.enable()


def setup_logging() -> None:
    """Set up logging configuration."""
    handlers: list[logging.Handler] = []
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers if handlers else [logging.NullHandler()],
    )

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("pytchat").setLevel(logging.WARNING)


def main() -> int:
    """Main entry point."""
    setup_logging()

    try:
        from livestream_list.gui.app import run

        return run()
    except ImportError as e:
        logging.error(f"Failed to import GUI: {e}")
        logging.error("Make sure PySide6 is installed:")
        logging.error("  pip install PySide6")
        return 1


if __name__ == "__main__":
    sys.exit(main())
