"""Link tooltip preview cache with async fetching."""

import logging
import re
from collections import OrderedDict

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 200
FETCH_BYTES = 16384  # Read first 16KB to find <title>
FETCH_TIMEOUT = 5  # seconds

# Regex to extract <title> from HTML
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class LinkPreviewWorker(QThread):
    """Fetches a page title from a URL in a background thread."""

    preview_ready = Signal(str, str)  # url, title

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        """Fetch the URL and extract the page title."""
        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; LinkPreview/1.0)"},
            )
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                data = resp.read(FETCH_BYTES)
                # Try UTF-8, fall back to latin-1
                try:
                    html = data.decode("utf-8", errors="replace")
                except Exception:
                    html = data.decode("latin-1", errors="replace")
                match = _TITLE_RE.search(html)
                if match:
                    title = match.group(1).strip()
                    # Clean up HTML entities
                    import html as html_mod

                    title = html_mod.unescape(title)
                    # Truncate long titles
                    if len(title) > 100:
                        title = title[:97] + "..."
                    self.preview_ready.emit(self._url, title)
                else:
                    self.preview_ready.emit(self._url, "")
        except Exception:
            self.preview_ready.emit(self._url, "")


class LinkPreviewCache:
    """Cache for link preview titles with async background fetching."""

    def __init__(self):
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._pending: set[str] = set()
        self._workers: list[LinkPreviewWorker] = []

    def get_or_fetch(self, url: str, parent=None) -> str | None:
        """Get cached title or start a background fetch.

        Returns:
            The cached title string if available, None if fetch is pending.
            Empty string means the fetch completed but no title was found.
        """
        if url in self._cache:
            return self._cache[url]
        if url in self._pending:
            return None
        # Start background fetch
        self._pending.add(url)
        worker = LinkPreviewWorker(url, parent=parent)
        worker.preview_ready.connect(self._on_preview_ready)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        self._workers.append(worker)
        worker.start()
        return None

    def _on_preview_ready(self, url: str, title: str) -> None:
        """Handle a completed preview fetch."""
        self._pending.discard(url)
        self._cache[url] = title
        # Evict oldest entries if cache is too large
        while len(self._cache) > MAX_CACHE_SIZE:
            self._cache.popitem(last=False)

    def _cleanup_worker(self, worker: LinkPreviewWorker) -> None:
        """Remove a finished worker from the list."""
        try:
            self._workers.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()
