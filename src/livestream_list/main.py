#!/usr/bin/env python3
"""Main entry point for Livestream List."""

import argparse
import faulthandler
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

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


def configure_file_logging(enabled: bool, log_directory: str, log_level: str) -> None:
    """Configure or remove the rotating file handler on the root logger.

    Can be called at startup and again at runtime when settings change.
    """
    root = logging.getLogger()

    # Remove any existing RotatingFileHandler
    for handler in root.handlers[:]:
        if isinstance(handler, RotatingFileHandler):
            handler.close()
            root.removeHandler(handler)

    if not enabled:
        root.setLevel(logging.INFO)
        return

    # Resolve log directory
    if log_directory:
        log_dir = Path(log_directory)
    else:
        from livestream_list.core.settings import get_data_dir

        log_dir = get_data_dir() / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "livestream-list-qt.log"

    level = getattr(logging, log_level.upper(), logging.INFO)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=2,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.addHandler(handler)

    # Set root level to minimum of INFO (console) and file level
    root.setLevel(min(level, logging.INFO))


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
        from livestream_list.core.settings import Settings

        settings = Settings.load()
        configure_file_logging(
            enabled=settings.logging.enabled,
            log_directory=settings.logging.log_directory,
            log_level=settings.logging.log_level,
        )

        from livestream_list.gui.app import run

        return run(allow_multiple=args.allow_multiple)
    except ImportError as e:
        logging.error(f"Failed to import GUI: {e}")
        logging.error("Make sure PySide6 is installed:")
        logging.error("  pip install PySide6")
        return 1


if __name__ == "__main__":
    sys.exit(main())
