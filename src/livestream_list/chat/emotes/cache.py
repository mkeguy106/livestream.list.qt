"""Two-tier emote cache (memory LRU + disk)."""

import hashlib
import logging
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QPixmap

from ...core.settings import get_data_dir

logger = logging.getLogger(__name__)

CACHE_DIR_NAME = "emote_cache"
DEFAULT_EMOTE_HEIGHT = 28
MAX_MEMORY_ENTRIES = 2000


def _get_cache_dir() -> Path:
    """Get the emote disk cache directory."""
    cache_dir = get_data_dir() / CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_key_to_filename(key: str) -> str:
    """Convert a cache key to a safe filename."""
    return hashlib.md5(key.encode()).hexdigest() + ".png"


class EmoteCache(QObject):
    """Two-tier emote cache.

    - Memory: OrderedDict with LRU eviction (max 2000 entries)
    - Disk: PNG files in ~/.local/share/livestream-list-qt/emote_cache/

    Emotes not in cache are loaded asynchronously via EmoteLoaderWorker.
    """

    # Emitted when an emote is loaded and ready for display
    emote_loaded = Signal(str)  # cache key

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._memory: OrderedDict[str, QPixmap] = OrderedDict()
        self._cache_dir = _get_cache_dir()
        self._pending: set[str] = set()  # Keys currently being loaded
        self._loader: EmoteLoaderWorker | None = None

    @property
    def pixmap_dict(self) -> dict[str, QPixmap]:
        """Get the memory cache dict (for delegate access)."""
        return self._memory

    def get(self, key: str) -> QPixmap | None:
        """Get an emote pixmap from cache.

        Checks memory first, then disk. Returns None if not cached.
        """
        # Check memory
        if key in self._memory:
            self._memory.move_to_end(key)
            return self._memory[key]

        # Check disk
        filename = _cache_key_to_filename(key)
        disk_path = self._cache_dir / filename
        if disk_path.exists():
            pixmap = QPixmap(str(disk_path))
            if not pixmap.isNull():
                self._put_memory(key, pixmap)
                return pixmap

        return None

    def put(self, key: str, pixmap: QPixmap) -> None:
        """Store an emote pixmap in both memory and disk cache."""
        if pixmap.isNull():
            return

        # Scale to standard height
        if pixmap.height() != DEFAULT_EMOTE_HEIGHT:
            pixmap = pixmap.scaledToHeight(
                DEFAULT_EMOTE_HEIGHT,
                mode=Qt.TransformationMode.SmoothTransformation,
            )

        self._put_memory(key, pixmap)
        self._put_disk(key, pixmap)
        self._pending.discard(key)
        self.emote_loaded.emit(key)

    def has(self, key: str) -> bool:
        """Check if a key is in cache (memory or disk)."""
        if key in self._memory:
            return True
        filename = _cache_key_to_filename(key)
        return (self._cache_dir / filename).exists()

    def is_pending(self, key: str) -> bool:
        """Check if a key is currently being loaded."""
        return key in self._pending

    def mark_pending(self, key: str) -> None:
        """Mark a key as being loaded."""
        self._pending.add(key)

    def _put_memory(self, key: str, pixmap: QPixmap) -> None:
        """Store in memory cache with LRU eviction."""
        self._memory[key] = pixmap
        self._memory.move_to_end(key)

        # Evict oldest entries if over limit
        while len(self._memory) > MAX_MEMORY_ENTRIES:
            self._memory.popitem(last=False)

    def _put_disk(self, key: str, pixmap: QPixmap) -> None:
        """Store on disk."""
        filename = _cache_key_to_filename(key)
        disk_path = self._cache_dir / filename
        try:
            pixmap.save(str(disk_path), "PNG")
        except Exception as e:
            logger.debug(f"Failed to save emote to disk: {e}")

    def clear_memory(self) -> None:
        """Clear the memory cache."""
        self._memory.clear()

    def clear_disk(self) -> None:
        """Clear the disk cache."""
        try:
            for f in self._cache_dir.iterdir():
                f.unlink()
        except Exception as e:
            logger.error(f"Failed to clear disk cache: {e}")


class EmoteLoaderWorker(QThread):
    """Worker thread for downloading and decoding emote images.

    Processes a queue of (key, url) pairs, downloads images via aiohttp,
    and stores them in the EmoteCache.
    """

    emote_ready = Signal(str, object)  # key, QPixmap (or bytes for main thread decode)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._queue: list[tuple[str, str]] = []
        self._should_stop = False

    def enqueue(self, key: str, url: str) -> None:
        """Add an emote to the download queue."""
        self._queue.append((key, url))

    def stop(self) -> None:
        """Stop the worker."""
        self._should_stop = True

    def run(self) -> None:
        """Process the download queue."""
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._process_queue())
        finally:
            loop.close()

    async def _process_queue(self) -> None:
        """Download emotes from the queue."""
        import aiohttp

        async with aiohttp.ClientSession() as session:
            while self._queue and not self._should_stop:
                key, url = self._queue.pop(0)
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            pixmap = QPixmap()
                            if pixmap.loadFromData(data):
                                # Scale to standard height
                                if pixmap.height() > 0 and pixmap.height() != DEFAULT_EMOTE_HEIGHT:
                                    pixmap = pixmap.scaledToHeight(
                                        DEFAULT_EMOTE_HEIGHT,
                                        mode=Qt.TransformationMode.SmoothTransformation,
                                    )
                                self.emote_ready.emit(key, pixmap)
                except Exception as e:
                    logger.debug(f"Failed to download emote {key}: {e}")
