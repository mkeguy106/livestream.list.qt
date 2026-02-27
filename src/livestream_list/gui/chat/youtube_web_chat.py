"""YouTube chat widget using embedded QWebEngineView."""

from __future__ import annotations

import html
import logging
import re
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...core.models import Livestream
from ...core.settings import get_data_dir
from ..theme import get_theme, is_dark_mode
from .chat_widget import DismissibleBanner

if TYPE_CHECKING:
    from PySide6.QtWebEngineCore import QWebEngineProfile

    from ...core.settings import BuiltinChatSettings

logger = logging.getLogger(__name__)

# Pattern for !commands in stream titles
_COMMAND_RE = re.compile(r"(![a-zA-Z]\w*)")

# QWebEngine imports are deferred to avoid crashing Flatpak builds
# where libgssapi_krb5.so.2 may be missing from the runtime.
_webengine_available: bool | None = None

# Shared persistent profile (singleton)
_shared_profile: QWebEngineProfile | None = None


def _ensure_webengine() -> bool:
    """Lazily import QWebEngine modules. Returns True if available."""
    global _webengine_available
    if _webengine_available is not None:
        return _webengine_available
    try:
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: F401
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

        _webengine_available = True
    except ImportError:
        logger.warning("QWebEngine not available — YouTube embedded chat disabled")
        _webengine_available = False
    return _webengine_available


# Persistent cookie tracker — maintains a live dict of YouTube/Google cookies
# so we never need loadAllCookies() + QEventLoop timing hacks.
_tracked_cookies: dict[str, str] = {}  # name -> value
_tracker_connected: bool = False


def _on_cookie_added(cookie) -> None:
    """Track YouTube/Google cookies as they're added to the profile."""
    domain = cookie.domain()
    if "youtube.com" in domain or "google.com" in domain:
        name = cookie.name().data().decode()
        value = cookie.value().data().decode()
        _tracked_cookies[name] = value


def _on_cookie_removed(cookie) -> None:
    """Track YouTube/Google cookies as they're removed from the profile."""
    domain = cookie.domain()
    if "youtube.com" in domain or "google.com" in domain:
        name = cookie.name().data().decode()
        _tracked_cookies.pop(name, None)


def _get_shared_profile() -> QWebEngineProfile | None:
    """Get or create the shared persistent QWebEngineProfile for YouTube chat.

    Returns None if QWebEngine is not available (e.g. Flatpak missing libs).
    """
    global _shared_profile, _tracker_connected
    if not _ensure_webengine():
        return None

    from PySide6.QtWebEngineCore import QWebEngineProfile

    if _shared_profile is None:
        storage_path = str(get_data_dir() / "webengine")
        _shared_profile = QWebEngineProfile("youtube_chat")
        _shared_profile.setCachePath(storage_path)
        _shared_profile.setPersistentStoragePath(storage_path)

    if not _tracker_connected:
        _tracker_connected = True
        store = _shared_profile.cookieStore()
        store.cookieAdded.connect(_on_cookie_added)
        store.cookieRemoved.connect(_on_cookie_removed)
        # Load existing cookies from disk so the tracker is populated
        store.loadAllCookies()

    return _shared_profile


def get_youtube_cookie_string() -> str:
    """Extract YouTube cookies from the shared profile as 'name=value; ...' string."""
    # Ensure profile and tracker are initialized
    if _get_shared_profile() is None:
        return ""
    if not _tracked_cookies:
        return ""
    return "; ".join(f"{name}={value}" for name, value in _tracked_cookies.items())


def has_youtube_login() -> bool:
    """Check if the shared profile has YouTube auth cookies (SID present)."""
    if _get_shared_profile() is None:
        return False
    return "SID" in _tracked_cookies


def clear_youtube_cookies() -> None:
    """Clear all cookies from the shared YouTube profile."""
    profile = _get_shared_profile()
    if profile is not None:
        profile.cookieStore().deleteAllCookies()
    _tracked_cookies.clear()


class YouTubeWebChatWidget(QWidget):
    """YouTube chat widget using an embedded web view.

    Loads YouTube's native popout chat URL in a QWebEngineView.
    YouTube handles reading, sending, rendering, and auth.
    Includes title and socials banners matching the native ChatWidget.
    """

    popout_requested = Signal(str)  # channel_key
    settings_clicked = Signal()

    def __init__(
        self,
        channel_key: str,
        livestream: Livestream,
        settings: BuiltinChatSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.channel_key = channel_key
        self.livestream = livestream
        self.settings = settings
        self._is_dm = False
        self._dark_mode = is_dark_mode()
        self._socials: dict[str, str] = {}
        self._title_dismissed = False
        self._socials_dismissed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Stream title banner
        self._title_banner = DismissibleBanner(clickable_links=True)
        self._title_banner.dismissed.connect(self._on_title_dismissed)
        layout.addWidget(self._title_banner)

        # Socials banner
        self._socials_banner = DismissibleBanner(clickable_links=True)
        self._socials_banner.dismissed.connect(self._on_socials_dismissed)
        self._socials_banner.hide()
        layout.addWidget(self._socials_banner)

        profile = _get_shared_profile()
        if not livestream.video_id or not profile:
            # No active livestream or QWebEngine unavailable — show placeholder
            self._web_view = None
            msg = "No active livestream" if not profile else "YouTube embedded chat unavailable"
            placeholder = QLabel(msg)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(placeholder, 1)
        else:
            from PySide6.QtWebEngineCore import QWebEnginePage
            from PySide6.QtWebEngineWidgets import QWebEngineView

            # Create web view with shared persistent profile
            page = QWebEnginePage(profile, self)
            self._web_view = QWebEngineView(self)
            self._web_view.setPage(page)
            self._web_view.setUrl(self._build_url())
            self._web_view.loadFinished.connect(self._on_load_finished)
            layout.addWidget(self._web_view, 1)

        # Title refresh timer (viewer count / uptime updates)
        self._title_refresh_timer = QTimer(self)
        self._title_refresh_timer.setInterval(30_000)
        self._title_refresh_timer.timeout.connect(self._update_stream_title)

        # Apply banner styling and initial title
        self._update_banner_style()
        self._update_stream_title()
        if not self.settings.show_stream_title:
            self._title_banner.hide()
        if self.livestream and self.livestream.live:
            self._title_refresh_timer.start()

    def _build_url(self) -> QUrl:
        """Build the YouTube live chat popout URL."""
        url = f"https://www.youtube.com/live_chat?is_popout=1&v={self.livestream.video_id}"
        if self._dark_mode:
            url += "&dark_theme=1"
        return QUrl(url)

    def eventFilter(self, obj, event):  # noqa: N802
        """Intercept Ctrl+scroll on the Chromium render widget to handle zoom."""
        is_ctrl_scroll = (
            event.type() == QEvent.Type.Wheel
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        )
        if is_ctrl_scroll:
            page = self._web_view.page() if self._web_view else None
            if page:
                delta = event.angleDelta().y()
                factor = page.zoomFactor()
                if delta > 0:
                    factor = min(3.0, factor + 0.1)
                elif delta < 0:
                    factor = max(0.25, factor - 0.1)
                page.setZoomFactor(factor)
            return True  # Consume the event
        return super().eventFilter(obj, event)

    def _on_load_finished(self, ok: bool) -> None:
        """Handle page load completion."""
        if not ok:
            logger.warning(f"YouTube chat failed to load for {self.channel_key}")
            return
        # Install event filter on the Chromium render widget (exists only after first load)
        if self._web_view:
            render_widget = self._web_view.focusProxy()
            if render_widget:
                render_widget.installEventFilter(self)

    # --- Banner methods ---

    def _update_banner_style(self) -> None:
        """Apply banner colors from theme."""
        theme = get_theme()
        self._title_banner.applyBannerStyle(theme.chat_banner_bg, theme.chat_banner_text)
        self._socials_banner.applyBannerStyle(theme.chat_banner_bg, theme.chat_banner_text)

    def _update_stream_title(self) -> None:
        """Update the stream title banner from the livestream data."""
        title = ""
        if self.livestream and self.livestream.title:
            title = self.livestream.title

        if title and self.settings.show_stream_title and not self._title_dismissed:
            # Escape HTML and highlight !commands
            escaped = html.escape(title)
            html_title = _COMMAND_RE.sub(r"<b>\1</b>", escaped)

            # Append viewer count and uptime
            meta_parts: list[str] = []
            if self.livestream and self.livestream.live:
                if self.livestream.viewers:
                    meta_parts.append(f"\U0001f464 {self.livestream.viewers_str}")
                uptime = self.livestream.uptime_str
                if uptime:
                    meta_parts.append(f"\U0001f550 {uptime}")
            if meta_parts:
                meta_html = " &nbsp;\u00b7&nbsp; ".join(meta_parts)
                html_title += f'<br><span style="font-size: 10px; opacity: 0.7;">{meta_html}</span>'

            self._title_banner.setText(html_title)
            self._title_banner.setToolTip(title)
            self._title_banner.show()
        else:
            self._title_banner.hide()

    def _on_title_dismissed(self) -> None:
        """Handle title banner dismissal."""
        self._title_dismissed = True
        self._title_refresh_timer.stop()

    def _on_socials_dismissed(self) -> None:
        """Handle socials banner dismissal."""
        self._socials_dismissed = True

    # --- Public interface (called by ChatWindow) ---

    def apply_theme(self) -> None:
        """Reload if dark mode state changed."""
        self._update_banner_style()
        new_dark = is_dark_mode()
        if new_dark != self._dark_mode and self._web_view:
            self._dark_mode = new_dark
            self._web_view.setUrl(self._build_url())

    def update_banner_settings(self) -> None:
        """Update banner visibility after settings change."""
        self._update_banner_style()

        if self.settings.show_stream_title:
            self._title_dismissed = False
            self._update_stream_title()
        else:
            self._title_banner.hide()

        if self.settings.show_socials_banner:
            self._socials_dismissed = False
            if self._socials:
                self.set_socials(self._socials)
        else:
            self._socials_banner.hide()

    def update_livestream(self, livestream: Livestream) -> None:
        """Update stored livestream and refresh the title banner."""
        self.livestream = livestream
        self._update_stream_title()
        if livestream and livestream.live:
            self._title_refresh_timer.start()
        else:
            self._title_refresh_timer.stop()

    def set_socials(self, socials: dict) -> None:
        """Set channel socials and update the banner."""
        self._socials = socials

        if not socials or not self.settings.show_socials_banner or self._socials_dismissed:
            self._socials_banner.hide()
            return

        social_icons = {
            "discord": "\U0001f4ac",
            "instagram": "\U0001f4f7",
            "twitter": "\U0001f426",
            "x": "\U0001f426",
            "tiktok": "\U0001f3b5",
            "youtube": "\U0001f3ac",
            "facebook": "\U0001f465",
            "patreon": "\U0001f49b",
            "merch": "\U0001f455",
        }

        links = []
        for platform, url in socials.items():
            icon = social_icons.get(platform.lower(), "\U0001f517")
            label = platform.capitalize()
            links.append(f'{icon} <a href="{url}">{label}</a>')

        if links:
            self._socials_banner.setText("  ".join(links))
            self._socials_banner.show()
        else:
            self._socials_banner.hide()

    def cleanup(self) -> None:
        """Stop the page by navigating to about:blank."""
        self._title_refresh_timer.stop()
        if self._web_view:
            self._web_view.setUrl(QUrl("about:blank"))

    # --- No-op methods (called by ChatWindow signal handlers on all widgets) ---

    def add_messages(self, messages: list) -> None:  # noqa: ARG002
        pass

    def apply_moderation(self, event: object) -> None:  # noqa: ARG002
        pass

    def update_room_state(self, state: object) -> None:  # noqa: ARG002
        pass

    def set_connected(self) -> None:
        pass

    def set_disconnected(self) -> None:
        pass

    def set_reconnecting(self, delay: float = 0) -> None:  # noqa: ARG002
        pass

    def set_reconnect_failed(self) -> None:
        pass

    def set_authenticated(self, authenticated: bool = False) -> None:  # noqa: ARG002
        pass

    def show_error(self, message: str) -> None:  # noqa: ARG002
        pass

    def set_image_store(self, cache: object) -> None:  # noqa: ARG002
        pass

    def set_gif_timer(self, timer: object) -> None:  # noqa: ARG002
        pass

    def set_emote_map(self, *args: object) -> None:  # noqa: ARG002
        pass

    def load_disk_history(self, writer: object) -> None:  # noqa: ARG002
        pass

    def show_raid_banner(self, message: object) -> None:  # noqa: ARG002
        pass

    def get_all_messages(self) -> list:
        return []

    def repaint_messages(self) -> None:
        pass

    def invalidate_message_layout(self) -> None:
        pass

    def update_hype_train(self, event: object) -> None:  # noqa: ARG002
        pass

    def set_animation_enabled(self, enabled: bool) -> None:  # noqa: ARG002
        pass

    def set_spellcheck_enabled(self, enabled: bool) -> None:  # noqa: ARG002
        pass

    def set_autocorrect_enabled(self, enabled: bool) -> None:  # noqa: ARG002
        pass

    def has_animated_emotes(self) -> bool:
        return False
