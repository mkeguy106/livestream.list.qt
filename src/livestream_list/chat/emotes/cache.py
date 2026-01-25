"""Two-tier emote cache (memory LRU + disk)."""

import hashlib
import logging
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, QThread, Signal
from PySide6.QtGui import QImageReader, QPixmap

from ...core.settings import get_data_dir

logger = logging.getLogger(__name__)

CACHE_DIR_NAME = "emote_cache"
DEFAULT_EMOTE_HEIGHT = 28
MAX_MEMORY_ENTRIES = 2000
MAX_ANIMATED_ENTRIES = 100  # Animated emotes use significantly more memory (20-100 frames each)
MAX_DISK_CACHE_MB = 500  # Maximum disk cache size in MB
DISK_CACHE_EVICT_PERCENT = 0.2  # Evict 20% when over limit


def _get_cache_dir() -> Path:
    """Get the emote disk cache directory."""
    cache_dir = get_data_dir() / CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_key_to_filename(key: str) -> str:
    """Convert a cache key to a safe filename."""
    return hashlib.md5(key.encode()).hexdigest() + ".png"


def _cache_key_to_raw_filename(key: str) -> str:
    """Convert a cache key to a raw bytes filename (for animated emotes)."""
    return hashlib.md5(key.encode()).hexdigest() + ".raw"


def _is_animated(data: bytes) -> bool:
    """Quickly check if image data contains an animated image.

    Uses QImageReader metadata only (no pixel decoding) so it's safe
    to call from any thread.
    """
    if not data:
        return False

    byte_array = QByteArray(data)
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)

    reader = QImageReader(buffer)
    supports = reader.supportsAnimation()
    count = reader.imageCount()
    buffer.close()

    # supportsAnimation() + imageCount != 1 means animated
    # imageCount can be -1 (unknown) for animated formats, 0 (error), or >1
    return supports and count != 1


def _extract_frames(data: bytes) -> tuple[list[QPixmap], list[int]] | None:
    """Extract animation frames from raw image bytes.

    Returns (frames, delays) if the image has multiple frames, None otherwise.
    """
    if not data:
        return None

    byte_array = QByteArray(data)
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)

    reader = QImageReader(buffer)
    reader.setAutoTransform(True)

    # Check if the format supports animation. Note: imageCount() can return -1
    # (unknown) for some formats even when animated, so we rely on
    # supportsAnimation() and actually attempt to read multiple frames.
    image_count = reader.imageCount()
    if not reader.supportsAnimation() or image_count == 1:
        buffer.close()
        return None

    frames: list[QPixmap] = []
    delays: list[int] = []

    while reader.canRead():
        image = reader.read()
        if image.isNull():
            break
        pixmap = QPixmap.fromImage(image)
        if pixmap.height() != DEFAULT_EMOTE_HEIGHT and pixmap.height() > 0:
            pixmap = pixmap.scaledToHeight(
                DEFAULT_EMOTE_HEIGHT, mode=Qt.TransformationMode.SmoothTransformation
            )
        frames.append(pixmap)
        delays.append(max(reader.nextImageDelay(), 20))

    buffer.close()

    if len(frames) <= 1:
        return None

    return frames, delays


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
        # Use OrderedDict for LRU eviction of animated emotes (they use much more memory)
        self._animated: OrderedDict[str, list[QPixmap]] = OrderedDict()
        self._frame_delays: dict[str, list[int]] = {}  # key -> ms per frame
        self._cache_dir = _get_cache_dir()
        self._pending: set[str] = set()  # Keys currently being loaded
        self._loader: EmoteLoaderWorker | None = None

    @property
    def pixmap_dict(self) -> dict[str, QPixmap]:
        """Get the memory cache dict (for delegate access)."""
        return self._memory

    @property
    def animated_dict(self) -> dict[str, list[QPixmap]]:
        """Get the animated frame cache dict (for delegate access)."""
        return self._animated

    def put_animated(self, key: str, frames: list[QPixmap], delays: list[int]) -> None:
        """Store an animated emote's frame list with LRU eviction."""
        if not frames:
            return
        self._animated[key] = frames
        self._animated.move_to_end(key)
        self._frame_delays[key] = delays

        # Evict oldest animated entries if over limit
        while len(self._animated) > MAX_ANIMATED_ENTRIES:
            evicted_key, _ = self._animated.popitem(last=False)
            self._frame_delays.pop(evicted_key, None)

        # Also store first frame in static cache as fallback
        self._put_memory(key, frames[0])
        self._pending.discard(key)
        self.emote_loaded.emit(key)

    def get_frames(self, key: str) -> list[QPixmap] | None:
        """Get an animated emote's frame list."""
        return self._animated.get(key)

    def has_animated(self, key: str) -> bool:
        """Check if a key has animated frames."""
        return key in self._animated

    def has_animation_data(self, key: str) -> bool:
        """Check if we have animation-aware data for this key.

        Returns True if the key is in the animated dict or has a raw file on disk.
        Returns False if we only have a legacy PNG cache (needs re-download for
        animation detection).
        """
        if key in self._animated:
            return True
        raw_filename = _cache_key_to_raw_filename(key)
        return (self._cache_dir / raw_filename).exists()

    def get(self, key: str) -> QPixmap | None:
        """Get an emote pixmap from cache.

        Checks memory first, then disk. Returns None if not cached.
        For animated emotes on disk, re-extracts frames into the animated cache.
        """
        # Check memory
        if key in self._memory:
            self._memory.move_to_end(key)
            return self._memory[key]

        # Check disk - raw file first (preserves original format for animation)
        raw_filename = _cache_key_to_raw_filename(key)
        raw_path = self._cache_dir / raw_filename
        if raw_path.exists():
            try:
                data = raw_path.read_bytes()
                if not data:
                    pass  # Empty marker file, fall through to PNG
                else:
                    result = _extract_frames(data)
                    if result:
                        frames, delays = result
                        self._animated[key] = frames
                        self._frame_delays[key] = delays
                        self._put_memory(key, frames[0])
                        return frames[0]
                    else:
                        # Static emote stored as raw - load directly
                        pixmap = QPixmap()
                        if pixmap.loadFromData(data):
                            if pixmap.height() != DEFAULT_EMOTE_HEIGHT and pixmap.height() > 0:
                                pixmap = pixmap.scaledToHeight(
                                    DEFAULT_EMOTE_HEIGHT,
                                    mode=Qt.TransformationMode.SmoothTransformation,
                                )
                            self._put_memory(key, pixmap)
                            return pixmap
            except Exception as e:
                logger.debug(f"Failed to load emote from raw disk cache: {e}")

        # Check disk - static PNG
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
        if key in self._animated:
            return True
        raw_filename = _cache_key_to_raw_filename(key)
        if (self._cache_dir / raw_filename).exists():
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
            self._enforce_disk_limit()
        except Exception as e:
            logger.debug(f"Failed to save emote to disk: {e}")

    def _put_disk_raw(self, key: str, data: bytes) -> None:
        """Store raw bytes on disk (for animated emotes)."""
        filename = _cache_key_to_raw_filename(key)
        disk_path = self._cache_dir / filename
        try:
            disk_path.write_bytes(data)
            self._enforce_disk_limit()
        except Exception as e:
            logger.debug(f"Failed to save raw emote to disk: {e}")

    def _enforce_disk_limit(self) -> None:
        """Enforce disk cache size limit by evicting oldest files."""
        try:
            max_bytes = MAX_DISK_CACHE_MB * 1024 * 1024
            files = list(self._cache_dir.iterdir())

            # Calculate total size
            file_info = []
            total_size = 0
            for f in files:
                try:
                    stat = f.stat()
                    total_size += stat.st_size
                    file_info.append((f, stat.st_mtime, stat.st_size))
                except OSError:
                    continue

            if total_size <= max_bytes:
                return

            # Sort by modification time (oldest first)
            file_info.sort(key=lambda x: x[1])

            # Evict files until under limit (evict at least 20% to avoid frequent cleanups)
            target_size = int(max_bytes * (1 - DISK_CACHE_EVICT_PERCENT))
            evicted_count = 0

            for filepath, _mtime, size in file_info:
                if total_size <= target_size:
                    break
                try:
                    filepath.unlink()
                    total_size -= size
                    evicted_count += 1
                except OSError:
                    continue

            if evicted_count > 0:
                logger.info(
                    f"Disk cache cleanup: evicted {evicted_count} files, "
                    f"size now {total_size // (1024 * 1024)}MB"
                )
        except Exception as e:
            logger.debug(f"Disk cache cleanup error: {e}")

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
    and emits raw bytes via signals. QPixmap creation happens on the main
    thread to comply with Qt threading requirements.
    """

    # Both signals send raw bytes - QPixmap must be created on GUI thread
    emote_ready = Signal(str, bytes)  # key, raw_data (static image)
    animated_emote_ready = Signal(str, bytes)  # key, raw_data (frames extracted on main thread)

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
                            # Detect animated image without creating QPixmaps
                            # (QPixmap must be created on the GUI thread)
                            if _is_animated(data):
                                self.animated_emote_ready.emit(key, data)
                            else:
                                # Emit raw bytes - QPixmap creation happens on main thread
                                self.emote_ready.emit(key, data)
                except Exception as e:
                    logger.debug(f"Failed to download emote {key}: {e}")
