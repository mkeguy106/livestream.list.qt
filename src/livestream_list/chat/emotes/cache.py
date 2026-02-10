"""Two-tier emote cache (memory LRU + disk)."""

import hashlib
import logging
import queue
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

from ...core.settings import get_data_dir

logger = logging.getLogger(__name__)

CACHE_DIR_NAME = "emote_cache"
DEFAULT_EMOTE_HEIGHT = 28
MAX_MEMORY_ENTRIES = 2000
MAX_ANIMATED_ENTRIES = 300  # Animated emotes use significantly more memory (20-100 frames each)
DEFAULT_DISK_CACHE_MB = 500  # Default disk cache size in MB
DISK_CACHE_EVICT_PERCENT = 0.2  # Evict 20% when over limit
DOWNLOAD_PRIORITY_HIGH = 0
DOWNLOAD_PRIORITY_LOW = 1
MAX_EMOTE_DOWNLOAD_RETRIES = 2
CONCURRENT_EMOTE_DOWNLOADS = 10
FRAME_CONVERT_BASE_BUDGET_MS = 4.0
FRAME_CONVERT_MAX_BUDGET_MS = 8.0
FRAME_CONVERT_INTERVAL_MS = 16  # ~60fps, was 4ms which was too aggressive
FRAME_CONVERT_SLOW_BATCH_MS = 12.0
FRAME_CONVERT_LOG_THROTTLE_S = 5.0


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


def _encode_png_bytes(image: QImage) -> bytes | None:
    """Encode a QImage to PNG bytes."""
    if image is None or image.isNull():
        return None
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = image.save(buffer, "PNG")
    data = bytes(buffer.data()) if ok else None
    buffer.close()
    if not data:
        return None
    return data


def _is_nonempty_file(path: Path) -> bool:
    """Return True if a file exists and has non-zero size."""
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


def _decode_first_frame_image(data: bytes):
    """Decode just the first frame to a QImage."""
    if not data:
        return None
    byte_array = QByteArray(data)
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    image = reader.read()
    buffer.close()
    if image.isNull():
        return None
    return image


def _decode_image_data(data: bytes) -> tuple[QImage, bool] | None:
    """Decode image bytes to QImage + animated flag. Thread-safe (no QPixmap)."""
    if not data:
        return None
    is_animated = _is_animated(data)
    image = _decode_first_frame_image(data)
    if image is None or image.isNull():
        image = QImage.fromData(data)
    if image is None or image.isNull():
        return None
    return image, is_animated


def _extract_frame_images(data: bytes) -> tuple[list[QImage], list[int]] | None:
    """Extract animation frames as QImage objects."""
    if not data:
        return None

    byte_array = QByteArray(data)
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)

    reader = QImageReader(buffer)
    reader.setAutoTransform(True)

    image_count = reader.imageCount()
    if not reader.supportsAnimation() or image_count == 1:
        buffer.close()
        return None

    frames: list[QImage] = []
    delays: list[int] = []
    while reader.canRead():
        image = reader.read()
        if image.isNull():
            break
        frames.append(image)
        delays.append(max(reader.nextImageDelay(), 20))

    buffer.close()

    if len(frames) <= 1:
        return None

    return frames, delays


class EmoteFrameWorker(QThread):
    """Worker thread for loading animated emote bytes from disk.

    NOTE: Frame extraction (QImageReader) must happen on the main thread to avoid
    freezing the UI. This worker only reads raw bytes and emits them for main
    thread decoding.
    """

    bytes_ready = Signal(str, bytes)  # key, raw_data
    frames_ready = Signal(str, object, object)  # key, list[QImage], list[int] (legacy)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._queue: queue.Queue[tuple[str, Path] | None] = queue.Queue()
        self._pending: set[str] = set()
        self._should_stop = False

    def enqueue(self, key: str, path: Path) -> None:
        if key in self._pending:
            return
        self._pending.add(key)
        self._queue.put((key, path))

    def stop(self) -> None:
        self._should_stop = True
        self._queue.put(None)

    def run(self) -> None:
        while not self._should_stop:
            item = self._queue.get()
            if item is None:
                return
            key, path = item
            try:
                data = path.read_bytes()
                if data:
                    # Emit raw bytes - frame extraction happens on main thread
                    self.bytes_ready.emit(key, data)
            except Exception as e:
                logger.debug(f"Failed to read animated frames for {key}: {e}")
            finally:
                self._pending.discard(key)


class EmoteDiskLoaderWorker(QThread):
    """Worker thread for loading emote bytes from disk.

    NOTE: Image decoding (QImageReader) must happen on the main thread to avoid
    freezing the UI. This worker only reads raw bytes and emits them for main
    thread decoding.
    """

    # Emit raw bytes for main thread to decode
    bytes_ready = Signal(str, bytes)  # key, raw_data
    image_ready = Signal(str, object, object, bool)  # key, QImage, raw_data, is_animated (legacy)
    image_failed = Signal(str, str)  # key, reason

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._queue: queue.Queue[tuple[str, Path] | None] = queue.Queue()
        self._pending: set[str] = set()
        self._should_stop = False

    def enqueue(self, key: str, path: Path) -> None:
        if key in self._pending:
            return
        self._pending.add(key)
        self._queue.put((key, path))

    def stop(self) -> None:
        self._should_stop = True
        self._queue.put(None)

    def run(self) -> None:
        while not self._should_stop:
            item = self._queue.get()
            if item is None:
                return
            key, path = item
            try:
                data = path.read_bytes()
                if not data:
                    self.image_failed.emit(key, "empty")
                    continue
                # Emit raw bytes - decoding happens on main thread to avoid freeze
                self.bytes_ready.emit(key, data)
            except Exception as e:
                logger.debug(f"Failed to read emote from disk for {key}: {e}")
                self.image_failed.emit(key, "exception")
            finally:
                self._pending.discard(key)


class _DiskCacheWorker(threading.Thread):
    """Background worker for disk writes and eviction.

    Uses incremental size tracking to avoid full directory scans on every write.
    A full calibration scan runs once on startup and every 5 minutes thereafter.
    Eviction only triggers when the running total exceeds the configured limit.
    """

    _RECALIBRATE_INTERVAL_S = 300.0  # 5 minutes

    def __init__(self, cache_dir: Path, get_limit_bytes, logger_obj) -> None:
        super().__init__(daemon=True)
        self._cache_dir = cache_dir
        self._get_limit_bytes = get_limit_bytes
        self._logger = logger_obj
        self._queue: queue.Queue[tuple[str, Path | None, bytes | None]] = queue.Queue()
        self._stop_event = threading.Event()
        self._total_size_bytes: int = 0
        self._calibrated: bool = False
        self._last_calibration: float = 0.0

    @property
    def last_size_bytes(self) -> int:
        return self._total_size_bytes

    def enqueue_write(self, path: Path, data: bytes) -> None:
        self._queue.put(("write", path, data))

    def enqueue_enforce(self) -> None:
        self._queue.put(("enforce", None, None))

    def stop(self) -> None:
        self._stop_event.set()
        self._queue.put(("stop", None, None))

    def _calibrate(self) -> None:
        """Full size-only scan of the cache directory."""
        total = 0
        try:
            for f in self._cache_dir.iterdir():
                try:
                    total += f.stat().st_size
                except OSError:
                    continue
        except OSError as e:
            self._logger.debug(f"Disk cache calibration error: {e}")
        self._total_size_bytes = total
        self._calibrated = True
        self._last_calibration = time.monotonic()
        self._logger.info(
            f"Disk cache calibrated: {total // (1024 * 1024)}mb "
            f"({total:,} bytes)"
        )

    def _maybe_recalibrate(self) -> None:
        """Re-calibrate if enough time has elapsed."""
        now = time.monotonic()
        if now - self._last_calibration >= self._RECALIBRATE_INTERVAL_S:
            self._calibrate()

    def _run_eviction_if_needed(self) -> None:
        """Run eviction only if the running total exceeds the limit."""
        max_bytes = self._get_limit_bytes()
        if self._total_size_bytes > max_bytes:
            self._total_size_bytes = _enforce_disk_limit(
                self._cache_dir, max_bytes, self._logger
            )

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                kind, path, data = self._queue.get(timeout=1.0)
            except queue.Empty:
                # Idle — recalibrate if stale
                self._maybe_recalibrate()
                continue

            # Lazy first calibration on first queue item
            if not self._calibrated:
                self._calibrate()

            if kind == "stop":
                return
            if kind == "write" and path and data is not None:
                try:
                    path.write_bytes(data)
                except Exception as e:
                    self._logger.debug(f"Failed to write disk cache file: {e}")
                else:
                    self._total_size_bytes += len(data)
                self._run_eviction_if_needed()
            elif kind == "enforce":
                self._run_eviction_if_needed()


def _enforce_disk_limit(cache_dir: Path, max_bytes: int, logger_obj) -> int:
    """Enforce disk cache size limit by evicting oldest files."""
    try:
        files = list(cache_dir.iterdir())

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
            return total_size

        # Evict files until under limit (evict at least 20% to avoid frequent cleanups)
        # Prefer evicting PNGs first so raw (animation-capable) data survives longer.
        target_size = int(max_bytes * (1 - DISK_CACHE_EVICT_PERCENT))
        evicted_count = 0

        png_files = [info for info in file_info if info[0].suffix == ".png"]
        raw_files = [info for info in file_info if info[0].suffix == ".raw"]
        other_files = [info for info in file_info if info[0].suffix not in {".png", ".raw"}]

        # Sort by modification time (oldest first)
        png_files.sort(key=lambda x: x[1])
        raw_files.sort(key=lambda x: x[1])
        other_files.sort(key=lambda x: x[1])

        for filepath, _mtime, size in png_files + other_files + raw_files:
            if total_size <= target_size:
                break
            try:
                filepath.unlink()
                total_size -= size
                evicted_count += 1
            except OSError:
                continue

        if evicted_count > 0:
            logger_obj.info(
                f"Disk cache cleanup: evicted {evicted_count} files, "
                f"size now {total_size // (1024 * 1024)}MB"
            )
        return total_size
    except Exception as e:
        logger_obj.debug(f"Disk cache cleanup error: {e}")
        return 0


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
        self._pending_download: set[str] = set()  # Keys currently being downloaded
        self._pending_decode: set[str] = set()  # Keys currently being decoded from disk
        self._loader: EmoteLoaderWorker | None = None
        self._download_queue: list[tuple[str, str, int]] = []
        self._download_attempts: dict[tuple[str, str], int] = {}
        self._download_fallbacks: dict[tuple[str, str], str] = {}
        self._last_download_url: dict[str, str] = {}
        self._download_blocked_until: dict[tuple[str, str], float] = {}
        self._no_animation: set[str] = set()
        self._last_access: dict[str, float] = {}
        self._disk_cache_mb = DEFAULT_DISK_CACHE_MB
        self._disk_lock = threading.Lock()
        self._disk_worker = _DiskCacheWorker(self._cache_dir, self._get_disk_limit_bytes, logger)
        self._disk_worker.start()
        self._frame_worker: EmoteFrameWorker | None = None
        self._frame_convert_queue: deque[tuple[str, list[QImage], list[int]]] = deque()
        self._frame_convert_pending: set[str] = set()
        self._frame_convert_current: dict | None = None
        self._frame_convert_timer = QTimer(self)
        self._frame_convert_timer.setInterval(FRAME_CONVERT_INTERVAL_MS)
        self._frame_convert_timer.timeout.connect(self._process_frame_conversions)
        self._frame_convert_last_log = 0.0
        self._disk_loader: EmoteDiskLoaderWorker | None = None

        # Queue for decoding downloaded emotes on main thread (avoids freeze)
        self._decode_queue: deque[tuple[str, bytes]] = deque()
        self._decode_timer = QTimer(self)
        self._decode_timer.setInterval(8)  # Fast processing (8ms interval)
        self._decode_timer.timeout.connect(self._process_decode_queue)

        # Queue for extracting animation frames on main thread
        self._frame_extract_queue: deque[tuple[str, bytes]] = deque()
        self._frame_extract_timer = QTimer(self)
        self._frame_extract_timer.setInterval(8)  # Fast processing (8ms interval)
        self._frame_extract_timer.timeout.connect(self._process_frame_extract_queue)

    @property
    def pixmap_dict(self) -> dict[str, QPixmap]:
        """Get the memory cache dict (for delegate access)."""
        return self._memory

    @property
    def animated_dict(self) -> dict[str, list[QPixmap]]:
        """Get the animated frame cache dict (for delegate access)."""
        return self._animated

    def has_in_memory(self, key: str) -> bool:
        """Check if a key is currently loaded in memory (static or animated)."""
        return key in self._memory or key in self._animated

    def _touch(self, key: str) -> None:
        """Update last access timestamp for eviction tracking."""
        self._last_access[key] = time.monotonic()

    def expire_older_than(self, max_age_seconds: float) -> int:
        """Evict memory entries older than max_age_seconds. Returns count evicted."""
        now = time.monotonic()
        expired = [key for key, ts in self._last_access.items() if (now - ts) > max_age_seconds]
        for key in expired:
            self._memory.pop(key, None)
            self._animated.pop(key, None)
            self._frame_delays.pop(key, None)
            self._last_access.pop(key, None)
        return len(expired)

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
        self._pending_download.discard(key)
        self._touch(key)
        self.emote_loaded.emit(key)

    def get_frames(self, key: str) -> list[QPixmap] | None:
        """Get an animated emote's frame list."""
        if key in self._animated:
            self._animated.move_to_end(key)
            self._touch(key)
            return self._animated[key]
        return None

    def get_frame_delays(self, key: str) -> list[int] | None:
        """Get per-frame delays (ms) for an animated emote."""
        return self._frame_delays.get(key)

    def set_disk_limit_mb(self, mb: int) -> None:
        """Set the disk cache size limit (MB)."""
        with self._disk_lock:
            self._disk_cache_mb = max(50, min(5000, int(mb)))
        self._disk_worker.enqueue_enforce()

    def touch_animated(self, key: str) -> None:
        """Mark an animated emote as recently used to reduce eviction."""
        if key in self._animated:
            self._animated.move_to_end(key)

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
        raw_path = self._cache_dir / raw_filename
        return _is_nonempty_file(raw_path)

    def request_animation_frames(self, key: str) -> None:
        """Decode animation frames in the background if raw data is available."""
        if key in self._animated:
            return
        raw_filename = _cache_key_to_raw_filename(key)
        raw_path = self._cache_dir / raw_filename
        if not _is_nonempty_file(raw_path):
            return
        if not self._frame_worker:
            self._frame_worker = EmoteFrameWorker(parent=self)
            self._frame_worker.bytes_ready.connect(self._on_frame_bytes_ready)
            self._frame_worker.start()
        self._frame_worker.enqueue(key, raw_path)

    def get(self, key: str) -> QPixmap | None:
        """Get an emote pixmap from cache.

        Checks memory first, then disk. Returns None if not cached.
        For animated emotes on disk, re-extracts frames into the animated cache.
        """
        # Check memory
        if key in self._memory:
            self._memory.move_to_end(key)
            self._touch(key)
            return self._memory[key]

        # Check disk - raw file first (preserves original format for animation)
        raw_filename = _cache_key_to_raw_filename(key)
        raw_path = self._cache_dir / raw_filename
        if _is_nonempty_file(raw_path):
            self._request_disk_load(key, raw_path)
            return None

        # Check disk - static PNG
        filename = _cache_key_to_filename(key)
        disk_path = self._cache_dir / filename
        if disk_path.exists():
            self._request_disk_load(key, disk_path)
            return None

        return None

    def put(
        self,
        key: str,
        pixmap: QPixmap,
        raw_data: bytes | None = None,
        png_bytes: bytes | None = None,
    ) -> None:
        """Store an emote pixmap in memory and disk cache."""
        if pixmap.isNull():
            return

        self._put_memory(key, pixmap)
        if raw_data:
            self._put_disk_raw(key, raw_data)
        if png_bytes:
            self._put_disk_bytes(key, png_bytes)
        elif not raw_data:
            self._put_disk(key, pixmap)
        self._pending_download.discard(key)
        self.emote_loaded.emit(key)

    def request(
        self,
        key: str,
        url: str,
        priority: int = DOWNLOAD_PRIORITY_HIGH,
        fallback_url: str | None = None,
        animated: bool = False,
    ) -> None:
        """Request a download if the key is not already cached."""
        if not url:
            return
        if animated and key in self._no_animation:
            if fallback_url:
                url = fallback_url
            animated = False
        if self.has_in_memory(key):
            return
        if self.is_pending(key):
            return
        if self.has(key) and not (animated and not self.has_animation_data(key)):
            # Trigger async disk load (non-blocking)
            self.get(key)
            return
        self.mark_pending(key)
        self._queue_download(key, url, priority, fallback_url=fallback_url)
        if self._download_queue:
            self._start_downloads()

    def _queue_download(
        self, key: str, url: str, priority: int, fallback_url: str | None = None
    ) -> None:
        """Queue a download with retry/fallback metadata."""
        if not url:
            return

        attempt_key = (key, url)
        blocked_until = self._download_blocked_until.get(attempt_key, 0)
        if blocked_until > time.monotonic():
            return
        attempts = self._download_attempts.get(attempt_key, 0)
        if attempts >= MAX_EMOTE_DOWNLOAD_RETRIES:
            logger.debug(f"Skipping download for {key} (attempts exceeded): {url}")
            self._download_blocked_until[attempt_key] = time.monotonic() + 60
            self.clear_pending(key)
            return

        self._last_download_url[key] = url
        if fallback_url and fallback_url != url:
            self._download_fallbacks[attempt_key] = fallback_url

        self._download_queue.append((key, url, priority))

    def _start_downloads(self) -> None:
        """Start or continue the emote loader worker."""
        if self._loader and self._loader.isRunning():
            # Worker is running; enqueue items directly
            for key, url, priority in self._download_queue:
                self._loader.enqueue(key, url, priority)
            self._download_queue.clear()
            return

        # Create new loader
        self._loader = EmoteLoaderWorker(parent=self)
        self._loader.bytes_ready.connect(self._on_download_bytes_ready)
        self._loader.emote_failed.connect(self._on_emote_failed)
        self._loader.finished.connect(self._on_loader_finished)

        for key, url, priority in self._download_queue:
            self._loader.enqueue(key, url, priority)
        self._download_queue.clear()

        self._loader.start()

    def _on_download_bytes_ready(self, key: str, data: bytes) -> None:
        """Handle downloaded emote bytes — queue for budgeted main-thread decoding."""
        if not data:
            self._handle_download_failure(key, "empty")
            return
        self._decode_queue.append((key, data))
        if not self._decode_timer.isActive():
            self._decode_timer.start()

    def _process_decode_queue(self) -> None:
        """Decode emotes (from downloads or disk) in budgeted batches."""
        start_time = time.monotonic()
        budget_ms = 30.0

        while self._decode_queue:
            key, data = self._decode_queue.popleft()

            result = _decode_image_data(data)
            if result is None:
                self._handle_download_failure(key, "decode")
                continue

            image, is_animated = result
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                self._handle_download_failure(key, "decode")
                continue

            if is_animated:
                self._no_animation.discard(key)
            else:
                last_url = self._last_download_url.get(key)
                if last_url and self._is_fallback_url(key, last_url):
                    self._no_animation.add(key)
            # Store original bytes directly — skip PNG re-encode
            self.put(key, pixmap, raw_data=data)
            self._clear_download_attempts(key)

            elapsed_ms = (time.monotonic() - start_time) * 1000
            if elapsed_ms >= budget_ms:
                break

        if not self._decode_queue:
            self._decode_timer.stop()

    def _on_emote_downloaded(
        self, key: str, raw_data: bytes, image, png_bytes: bytes | None
    ) -> None:
        """Handle a downloaded emote/badge image - create QPixmap on main thread (legacy)."""
        if not raw_data:
            self._handle_download_failure(key, "empty")
            return
        if image is None or image.isNull():
            self._handle_download_failure(key, "decode")
            return

        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._handle_download_failure(key, "decode")
            return

        last_url = self._last_download_url.get(key)
        if last_url and self._is_fallback_url(key, last_url):
            self._no_animation.add(key)

        self.put(key, pixmap, png_bytes=png_bytes)
        self._clear_download_attempts(key)

    def _on_animated_emote_downloaded(
        self, key: str, raw_data: bytes, image, png_bytes: bytes | None
    ) -> None:
        """Handle a downloaded animated emote - store raw data (legacy)."""
        if not raw_data:
            self._handle_download_failure(key, "empty")
            return
        if image is None or image.isNull():
            self._handle_download_failure(key, "decode")
            return

        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._handle_download_failure(key, "decode")
            return

        self._no_animation.discard(key)
        self.put(key, pixmap, raw_data=raw_data, png_bytes=png_bytes)
        self._clear_download_attempts(key)

    def _on_emote_failed(self, key: str, url: str, status: int, reason: str) -> None:
        """Handle download failures from the loader."""
        self._handle_download_failure(key, reason, url=url, status=status)

    def _handle_download_failure(
        self, key: str, reason: str, url: str | None = None, status: int = 0
    ) -> None:
        """Clear pending state and schedule retries/fallbacks for failed downloads."""
        self.clear_pending(key)

        if not url:
            url = self._last_download_url.get(key, "")
        if not url:
            return

        attempt_key = (key, url)
        attempts = self._download_attempts.get(attempt_key, 0) + 1
        self._download_attempts[attempt_key] = attempts

        fallback_url = self._download_fallbacks.get(attempt_key)
        if fallback_url and (status == 404 or reason in {"decode", "empty"}):
            logger.debug(f"Animated emote missing, falling back to static: {key}")
            self._schedule_retry(key, fallback_url, delay_ms=150)
            return

        if attempts < MAX_EMOTE_DOWNLOAD_RETRIES:
            delay_ms = 200 + attempts * 300
            logger.debug(
                f"Retrying emote download ({attempts}/{MAX_EMOTE_DOWNLOAD_RETRIES}) "
                f"{key} due to {reason}"
            )
            self._schedule_retry(key, url, delay_ms=delay_ms)

    def _schedule_retry(self, key: str, url: str, delay_ms: int) -> None:
        """Schedule a retry for a failed download."""
        from PySide6.QtCore import QTimer

        QTimer.singleShot(delay_ms, lambda: self._retry_download(key, url))

    def _retry_download(self, key: str, url: str) -> None:
        """Retry a download if still under retry limits."""
        attempt_key = (key, url)
        attempts = self._download_attempts.get(attempt_key, 0)
        if attempts >= MAX_EMOTE_DOWNLOAD_RETRIES:
            return
        self.mark_pending(key)
        self._queue_download(key, url, priority=DOWNLOAD_PRIORITY_HIGH)
        if self._download_queue:
            self._start_downloads()

    def _clear_download_attempts(self, key: str) -> None:
        """Clear attempt counters once a key succeeds."""
        for attempt_key in list(self._download_attempts.keys()):
            if attempt_key[0] == key:
                self._download_attempts.pop(attempt_key, None)
                self._download_fallbacks.pop(attempt_key, None)
                self._download_blocked_until.pop(attempt_key, None)
        self._last_download_url.pop(key, None)

    def _is_fallback_url(self, key: str, url: str) -> bool:
        """Return True if the url is a known fallback for the key."""
        for attempt_key, fallback in self._download_fallbacks.items():
            if attempt_key[0] == key and fallback == url:
                return True
        return False

    def _on_loader_finished(self) -> None:
        """Handle loader worker finishing."""
        if self._download_queue:
            self._start_downloads()

    def stop(self) -> None:
        """Stop background workers."""
        if self._loader:
            self._loader.stop()
            self._loader.wait(3000)
            self._loader = None
        if self._disk_loader:
            self._disk_loader.stop()
            self._disk_loader.wait(1000)
            self._disk_loader = None
        if self._frame_worker:
            self._frame_worker.stop()
            self._frame_worker.wait(1000)
            self._frame_worker = None
        if self._frame_convert_timer.isActive():
            self._frame_convert_timer.stop()
        self._frame_convert_queue.clear()
        self._frame_convert_pending.clear()
        self._frame_convert_current = None

    def has(self, key: str) -> bool:
        """Check if a key is in cache (memory or disk)."""
        if key in self._memory:
            return True
        if key in self._animated:
            return True
        raw_filename = _cache_key_to_raw_filename(key)
        raw_path = self._cache_dir / raw_filename
        if _is_nonempty_file(raw_path):
            return True
        filename = _cache_key_to_filename(key)
        return (self._cache_dir / filename).exists()

    def pending_count(self) -> int:
        """Return number of emotes currently pending download/decoding."""
        return len(self._pending_download) + len(self._pending_decode)

    def downloads_queued(self) -> int:
        """Return number of queued downloads."""
        return len(self._download_queue)

    def downloads_inflight(self) -> int:
        """Return 1 if a download worker is running, else 0."""
        return 1 if self._loader and self._loader.isRunning() else 0

    def get_disk_usage_bytes(self) -> int:
        """Return last known disk usage (approximate)."""
        return max(0, int(self._disk_worker.last_size_bytes))

    def is_pending(self, key: str) -> bool:
        """Check if a key is currently being loaded."""
        return key in self._pending_download

    def mark_pending(self, key: str) -> None:
        """Mark a key as being loaded."""
        self._pending_download.add(key)

    def clear_pending(self, key: str) -> None:
        """Clear a pending mark for a key (on failure or cancel)."""
        self._pending_download.discard(key)

    def _put_memory(self, key: str, pixmap: QPixmap) -> None:
        """Store in memory cache with LRU eviction."""
        self._memory[key] = pixmap
        self._memory.move_to_end(key)
        self._touch(key)

        # Evict oldest entries if over limit
        while len(self._memory) > MAX_MEMORY_ENTRIES:
            self._memory.popitem(last=False)

    def _on_frame_bytes_ready(self, key: str, data: bytes) -> None:
        """Handle raw bytes for animation - queue for batched extraction."""
        if not data:
            return
        # Queue for batched processing to avoid blocking UI
        self._frame_extract_queue.append((key, data))
        if not self._frame_extract_timer.isActive():
            self._frame_extract_timer.start()

    def _process_frame_extract_queue(self) -> None:
        """Extract animation frames in batches to avoid blocking the UI."""
        start_time = time.monotonic()
        # Higher budget for frame extraction - extracting all GIF frames is slow
        budget_ms = 50.0  # Allow up to 50ms per batch

        while self._frame_extract_queue:
            key, data = self._frame_extract_queue.popleft()

            # Extract frames on main thread (QImageReader is not thread-safe)
            result = _extract_frame_images(data)
            if result:
                frames, delays = result
                self._on_frames_ready(key, frames, delays)

            # Check time budget
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if elapsed_ms >= budget_ms:
                break

        if not self._frame_extract_queue:
            self._frame_extract_timer.stop()

    def _on_frames_ready(self, key: str, frames, delays) -> None:
        """Handle decoded animation frames - queue for conversion."""
        if not frames:
            return
        if key in self._frame_convert_pending:
            return
        self._frame_convert_pending.add(key)
        self._frame_convert_queue.append((key, frames, delays))
        if not self._frame_convert_timer.isActive():
            self._frame_convert_timer.start()

    def _process_frame_conversions(self) -> None:
        """Convert QImage frames to QPixmap in small batches to avoid UI stalls."""
        start_time = time.monotonic()
        backlog = len(self._frame_convert_queue) + (1 if self._frame_convert_current else 0)
        extra = min(FRAME_CONVERT_MAX_BUDGET_MS - FRAME_CONVERT_BASE_BUDGET_MS, backlog * 0.5)
        budget_ms = FRAME_CONVERT_BASE_BUDGET_MS + max(0.0, extra)
        budget_ms = min(budget_ms, FRAME_CONVERT_MAX_BUDGET_MS)

        while True:
            if self._frame_convert_current is None:
                if not self._frame_convert_queue:
                    self._frame_convert_timer.stop()
                    return
                key, frames, delays = self._frame_convert_queue.popleft()
                self._frame_convert_current = {
                    "key": key,
                    "frames": frames,
                    "delays": delays,
                    "index": 0,
                    "pixmaps": [],
                }

            current = self._frame_convert_current
            key = current["key"]
            frames = current["frames"]
            idx = current["index"]
            pixmaps = current["pixmaps"]

            # Convert frames within time budget
            while idx < len(frames):
                pixmaps.append(QPixmap.fromImage(frames[idx]))
                idx += 1
                elapsed_ms = (time.monotonic() - start_time) * 1000
                if elapsed_ms >= budget_ms:
                    break

            current["index"] = idx

            if idx >= len(frames):
                self._frame_convert_current = None
                self._frame_convert_pending.discard(key)
                self.put_animated(key, pixmaps, current["delays"])
                # Continue if time remains
                elapsed_ms = (time.monotonic() - start_time) * 1000
                if elapsed_ms >= FRAME_CONVERT_SLOW_BATCH_MS:
                    now = time.monotonic()
                    if (now - self._frame_convert_last_log) >= FRAME_CONVERT_LOG_THROTTLE_S:
                        self._frame_convert_last_log = now
                        logger.debug(
                            "Animated frame conversion batch took %.1fms (key=%s, backlog=%d)",
                            elapsed_ms,
                            key,
                            backlog,
                        )
                if elapsed_ms >= budget_ms:
                    return
            else:
                elapsed_ms = (time.monotonic() - start_time) * 1000
                if elapsed_ms >= FRAME_CONVERT_SLOW_BATCH_MS:
                    now = time.monotonic()
                    if (now - self._frame_convert_last_log) >= FRAME_CONVERT_LOG_THROTTLE_S:
                        self._frame_convert_last_log = now
                        logger.debug(
                            "Animated frame conversion batch took %.1fms (key=%s, backlog=%d)",
                            elapsed_ms,
                            key,
                            backlog,
                        )
                return

    def _request_disk_load(self, key: str, path: Path) -> None:
        """Queue a disk decode without blocking the GUI thread."""
        if key in self._pending_decode:
            return
        self._pending_decode.add(key)
        if not self._disk_loader:
            self._disk_loader = EmoteDiskLoaderWorker(parent=self)
            self._disk_loader.bytes_ready.connect(self._on_disk_bytes_ready)
            self._disk_loader.image_failed.connect(self._on_disk_emote_failed)
            self._disk_loader.start()
        self._disk_loader.enqueue(key, path)

    def _on_disk_bytes_ready(self, key: str, data: bytes) -> None:
        """Handle raw bytes from disk - queue for batched decoding."""
        self._pending_decode.discard(key)
        if not data:
            return
        # Queue for batched processing to avoid blocking UI
        self._decode_queue.append((key, data))
        if not self._decode_timer.isActive():
            self._decode_timer.start()

    def _on_disk_emote_failed(self, key: str, reason: str) -> None:
        self._pending_decode.discard(key)
        logger.debug(f"Disk decode failed for {key}: {reason}")

    def _put_disk(self, key: str, pixmap: QPixmap) -> None:
        """Store on disk."""
        filename = _cache_key_to_filename(key)
        disk_path = self._cache_dir / filename
        try:
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            pixmap.save(buffer, "PNG")
            data = bytes(buffer.data())
            buffer.close()
            if data:
                self._disk_worker.enqueue_write(disk_path, data)
        except Exception as e:
            logger.debug(f"Failed to save emote to disk: {e}")

    def _put_disk_bytes(self, key: str, data: bytes) -> None:
        """Store PNG bytes on disk."""
        if not data:
            return
        filename = _cache_key_to_filename(key)
        disk_path = self._cache_dir / filename
        try:
            self._disk_worker.enqueue_write(disk_path, data)
        except Exception as e:
            logger.debug(f"Failed to save emote PNG to disk: {e}")

    def _put_disk_raw(self, key: str, data: bytes) -> None:
        """Store raw bytes on disk (for animated emotes)."""
        filename = _cache_key_to_raw_filename(key)
        disk_path = self._cache_dir / filename
        try:
            self._disk_worker.enqueue_write(disk_path, data)
        except Exception as e:
            logger.debug(f"Failed to save raw emote to disk: {e}")

    def _get_disk_limit_bytes(self) -> int:
        with self._disk_lock:
            return int(self._disk_cache_mb) * 1024 * 1024

    def clear_memory(self) -> None:
        """Clear the memory cache."""
        self._memory.clear()

    def clear_disk(self) -> None:
        """Clear the disk cache."""
        try:
            for f in self._cache_dir.iterdir():
                f.unlink()
            self._no_animation.clear()
            self._download_attempts.clear()
            self._download_blocked_until.clear()
            self._disk_worker._total_size_bytes = 0
        except Exception as e:
            logger.error(f"Failed to clear disk cache: {e}")


class EmoteLoaderWorker(QThread):
    """Worker thread for downloading emote images.

    Processes a queue of (key, url) pairs, downloads images via aiohttp,
    and emits raw bytes via signals. Decoding happens on the main thread
    in budgeted batches to avoid UI freezes.
    """

    bytes_ready = Signal(str, bytes)  # key, raw_data
    emote_failed = Signal(str, str, int, str)  # key, url, status, reason

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._queue: queue.PriorityQueue[tuple[int, int, str, str]] = queue.PriorityQueue()
        self._seq = 0
        self._should_stop = False

    def enqueue(self, key: str, url: str, priority: int = 0) -> None:
        """Add an emote to the download queue."""
        self._seq += 1
        self._queue.put((priority, self._seq, key, url))

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
        """Download emotes from the queue with concurrent requests."""
        import asyncio

        import aiohttp

        sem = asyncio.Semaphore(CONCURRENT_EMOTE_DOWNLOADS)
        timeout = aiohttp.ClientTimeout(total=10)
        connector = aiohttp.TCPConnector(limit=20, limit_per_host=8)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks: set[asyncio.Task] = set()

            while not self._should_stop or tasks:
                # Drain available items from queue (non-blocking)
                if not self._should_stop:
                    while True:
                        try:
                            _pri, _seq, key, url = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        task = asyncio.create_task(
                            self._download_one(session, sem, timeout, key, url)
                        )
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)

                if tasks:
                    await asyncio.wait(
                        tasks, timeout=0.15, return_when=asyncio.FIRST_COMPLETED
                    )
                elif not self._should_stop:
                    await asyncio.sleep(0.1)

    async def _download_one(self, session, sem, timeout, key: str, url: str) -> None:
        """Download a single emote image."""
        async with sem:
            try:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status != 200:
                        self.emote_failed.emit(key, url, resp.status, "http")
                        return
                    data = await resp.read()
                    if not data:
                        self.emote_failed.emit(key, url, resp.status, "empty")
                        return
                    self.bytes_ready.emit(key, data)
            except Exception as e:
                logger.debug(f"Failed to download emote {key}: {e}")
                self.emote_failed.emit(key, url, 0, "exception")
