"""Chatterino-style image primitives for emotes and badges."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QPixmap

from .cache import DOWNLOAD_PRIORITY_HIGH, EmoteCache

logger = logging.getLogger(__name__)


class GifTimer(QObject):
    """Global animation timer (shared across chat widgets)."""

    tick = Signal(int)  # frame index

    def __init__(self, interval_ms: int = 100, parent: QObject | None = None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._interval_ms = interval_ms
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._on_tick)
        self._start_time = time.monotonic()

    @property
    def interval_ms(self) -> int:
        return self._interval_ms

    def start(self) -> None:
        if not self._timer.isActive():
            self._start_time = time.monotonic()
            self._timer.start()

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    def _on_tick(self) -> None:
        elapsed_ms = int((time.monotonic() - self._start_time) * 1000)
        self.tick.emit(elapsed_ms)


class ImageExpirationPool(QObject):
    """Time-based eviction for in-memory images."""

    def __init__(
        self,
        store: EmoteCache,
        max_age_seconds: int = 10 * 60,
        interval_ms: int = 60 * 1000,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._store = store
        self._max_age_seconds = max_age_seconds
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._expire)
        self._timer.start()

    def _expire(self) -> None:
        evicted = self._store.expire_older_than(self._max_age_seconds)
        if evicted:
            logger.debug(f"ImageExpirationPool evicted {evicted} entries")


@dataclass(frozen=True)
class ImageSpec:
    """Specification for an image source."""

    scale: int
    key: str
    url: str
    fallback_url: str | None = None
    animated: bool = False


@dataclass(frozen=True)
class ImageRef:
    """Reference to a single image source at a specific scale."""

    scale: int
    key: str
    url: str
    store: EmoteCache
    priority: int = DOWNLOAD_PRIORITY_HIGH
    fallback_url: str | None = None
    animated: bool = False

    def is_loaded(self) -> bool:
        return self.store.has_in_memory(self.key)

    def request(self, priority: int | None = None) -> None:
        self.store.request(
            self.key,
            self.url,
            priority=self.priority if priority is None else priority,
            fallback_url=self.fallback_url,
            animated=self.animated,
        )

    def pixmap_or_load(self) -> QPixmap | None:
        pixmap = self.store.get(self.key)
        if pixmap and not pixmap.isNull():
            return pixmap
        self.request()
        return None

    def frames_or_load(self) -> list[QPixmap] | None:
        frames = self.store.get_frames(self.key)
        if frames:
            return frames
        # If animation data exists on disk, request frames
        self.store.request_animation_frames(self.key)
        self.request()
        return None


class ImageSet:
    """Multi-scale image set that returns the best available image."""

    def __init__(self, images: dict[int, ImageRef] | dict[int, ImageSpec]):
        self._images: dict[int, ImageRef] = {}
        self._specs: dict[int, ImageSpec] = {}
        if images:
            sample = next(iter(images.values()))
            if isinstance(sample, ImageRef):
                self._images = dict(images)  # type: ignore[assignment]
            else:
                self._specs = dict(images)  # type: ignore[assignment]

    def bind(self, store: EmoteCache) -> ImageSet:
        """Return a new ImageSet with ImageRefs bound to the given store."""
        if self._images:
            return self
        images: dict[int, ImageRef] = {}
        for scale, spec in self._specs.items():
            images[scale] = ImageRef(
                scale=spec.scale,
                key=spec.key,
                url=spec.url,
                store=store,
                fallback_url=spec.fallback_url,
                animated=spec.animated,
            )
        return ImageSet(images)

    def get_image_or_loaded(self, scale: float = 2.0) -> ImageRef | None:
        """Return the best loaded ImageRef; request the desired scale if missing."""
        if not self._images and not self._specs:
            return None
        if not self._images:
            # Not bound yet
            return None

        scale_key = self._pick_scale(scale)
        preferred = self._images.get(scale_key)

        # Prefer the requested scale if loaded
        if preferred and preferred.is_loaded():
            return preferred

        # Fall back to any loaded image (closest scale)
        loaded = [img for img in self._images.values() if img.is_loaded()]
        if loaded:
            return self._pick_best_loaded(loaded, scale)

        # Nothing loaded yet; request preferred scale
        if preferred:
            preferred.request()
            return preferred
        return None

    def prefetch(self, scale: float = 2.0, priority: int = DOWNLOAD_PRIORITY_HIGH) -> None:
        if not self._images:
            return
        image = self._images.get(self._pick_scale(scale))
        if image:
            image.request(priority=priority)

    def all_images(self) -> list[ImageRef]:
        return list(self._images.values())

    def _pick_scale(self, scale: float) -> int:
        # Prefer smallest available scale >= requested; fall back to largest
        available = sorted(self._images.keys())
        if not available:
            return 2
        candidates = [s for s in available if s >= scale]
        return candidates[0] if candidates else available[-1]

    @staticmethod
    def _pick_best_loaded(loaded: list[ImageRef], scale: float) -> ImageRef:
        # Prefer smallest loaded scale >= requested; fall back to largest loaded
        candidates = [img for img in loaded if img.scale >= scale]
        if candidates:
            return min(candidates, key=lambda img: img.scale)
        return max(loaded, key=lambda img: img.scale)
