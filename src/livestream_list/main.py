#!/usr/bin/env python3
"""Main entry point for Livestream List."""

import argparse
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="livestream-list-qt",
        description="Monitor livestreams on Twitch, YouTube, Kick, and Chaturbate",
    )
    parser.add_argument(
        "-m",
        "--allow-multiple",
        action="store_true",
        default=False,
        help="Allow multiple instances of the application to run simultaneously",
    )
    return parser.parse_args(argv)


def main() -> int:
    """Main entry point."""
    setup_logging()
    args = parse_args()

    try:
        from livestream_list.gui.app import run

        return run(allow_multiple=args.allow_multiple)
    except ImportError as e:
        logging.error(f"Failed to import GUI: {e}")
        logging.error("Make sure PySide6 is installed:")
        logging.error("  pip install PySide6")
        return 1


if __name__ == "__main__":
    sys.exit(main())
