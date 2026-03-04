"""TikTok LIVE chat widget using embedded QWebEngineView."""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
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

# Chrome UA — TikTok's webcast API returns 403 for QWebEngine's default UA.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Pattern for !commands in stream titles
_COMMAND_RE = re.compile(r"(![a-zA-Z]\w*)")

# QWebEngine imports are deferred to avoid crashing Flatpak builds
_tiktok_webengine_available: bool | None = None

# Shared persistent profile (singleton) — separate from YouTube/Chaturbate
_tiktok_profile: QWebEngineProfile | None = None

# Persistent cookie tracker for TikTok
_tracked_cookies: dict[str, str] = {}  # name -> value
_tracker_connected: bool = False
_force_logged_out: bool = False


def _ensure_webengine() -> bool:
    """Lazily import QWebEngine modules. Returns True if available."""
    global _tiktok_webengine_available
    if _tiktok_webengine_available is not None:
        return _tiktok_webengine_available
    try:
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: F401
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

        _tiktok_webengine_available = True
    except ImportError:
        logger.warning("QWebEngine not available — TikTok embedded chat disabled")
        _tiktok_webengine_available = False
    return _tiktok_webengine_available


_request_interceptor = None  # singleton, created in _get_shared_profile


def _on_cookie_added(cookie) -> None:
    """Track TikTok cookies as they're added to the profile."""
    global _force_logged_out
    domain = cookie.domain()
    name = cookie.name().data().decode()
    if "tiktok.com" in domain:
        value = cookie.value().data().decode()
        _tracked_cookies[name] = value
        if name == "sessionid":
            _force_logged_out = False


def _on_cookie_removed(cookie) -> None:
    """Track TikTok cookie removals.

    We intentionally do NOT remove from _tracked_cookies here.
    QWebEngine fires cookieRemoved when rotating/refreshing cookies
    (remove old -> add new), which would cause login detection to flicker.
    Cookies are only cleared on explicit logout via clear_tiktok_cookies().
    """
    pass


def _get_shared_profile() -> QWebEngineProfile | None:
    """Get or create the shared persistent QWebEngineProfile for TikTok.

    Returns None if QWebEngine is not available.
    """
    global _tiktok_profile, _tracker_connected
    if not _ensure_webengine():
        return None

    from PySide6.QtWebEngineCore import QWebEngineProfile

    if _tiktok_profile is None:
        from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

        class _TikTokInterceptor(QWebEngineUrlRequestInterceptor):
            """Override Sec-CH-UA headers to match real Chrome.

            QWebEngine's default Sec-CH-UA reveals QtWebEngine, causing
            TikTok's webcast API to return 403 for CSRF token fetches.
            """

            _CH_UA = b'"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"'
            _CH_UA_FULL = (
                b'"Chromium";v="131.0.0.0", "Google Chrome";v="131.0.0.0"'
                b', "Not_A Brand";v="24.0.0.0"'
            )

            def interceptRequest(self, info) -> None:  # noqa: N802
                url = info.requestUrl().toString()
                info.setHttpHeader(b"Sec-CH-UA", self._CH_UA)
                info.setHttpHeader(b"Sec-CH-UA-Full-Version-List", self._CH_UA_FULL)
                info.setHttpHeader(b"Sec-CH-UA-Platform", b'"Linux"')
                info.setHttpHeader(b"Sec-CH-UA-Mobile", b"?0")
                # Log webcast/chat/send requests for diagnostics
                method = info.requestMethod().data().decode()
                if "webcast" in url or "chat" in url.split("?")[0] or method != "GET":
                    logger.info(
                        f"[TK-REQ] {method} {url[:150]} "
                        f"type={info.resourceType()}"
                    )

        global _request_interceptor
        storage_path = str(get_data_dir() / "webengine_tiktok")
        _tiktok_profile = QWebEngineProfile("tiktok_chat")
        _tiktok_profile.setCachePath(storage_path)
        _tiktok_profile.setPersistentStoragePath(storage_path)
        _tiktok_profile.setHttpUserAgent(_USER_AGENT)
        _request_interceptor = _TikTokInterceptor(_tiktok_profile)
        _tiktok_profile.setUrlRequestInterceptor(_request_interceptor)

    if not _tracker_connected:
        _tracker_connected = True
        store = _tiktok_profile.cookieStore()
        store.cookieAdded.connect(_on_cookie_added)
        store.cookieRemoved.connect(_on_cookie_removed)
        store.loadAllCookies()

    return _tiktok_profile


def _read_cookies_from_db() -> dict[str, str]:
    """Read TikTok cookies directly from the Chromium SQLite database."""
    import sqlite3

    db_path = Path(get_data_dir() / "webengine_tiktok" / "Cookies")
    if not db_path.exists():
        return {}

    cookies: dict[str, str] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.execute("SELECT name, value FROM cookies WHERE host_key LIKE '%tiktok.com'")
        for name, value in cursor:
            cookies[name] = value
        conn.close()
    except Exception as e:
        logger.debug(f"Could not read TikTok cookie DB: {e}")
    return cookies


def _get_all_cookies() -> dict[str, str]:
    """Get TikTok cookies from all sources."""
    cookies = _read_cookies_from_db()
    cookies.update(_tracked_cookies)
    return cookies


def has_tiktok_login() -> bool:
    """Check if the TikTok profile has auth cookies (sessionid present)."""
    if _force_logged_out:
        return False
    if "sessionid" in _tracked_cookies:
        return True
    cookies = _read_cookies_from_db()
    return "sessionid" in cookies


def clear_tiktok_cookies() -> None:
    """Clear all cookies from the shared TikTok profile."""
    global _force_logged_out
    profile = _get_shared_profile()
    if profile is not None:
        profile.cookieStore().deleteAllCookies()
    _tracked_cookies.clear()
    _force_logged_out = True


# Hide page content immediately at DocumentCreation to prevent flash.
# The cover div is removed by the isolation JS after chat is found.
_PRE_HIDE_JS = (
    "if(location.hostname&&location.hostname.indexOf('tiktok.com')>=0){"
    # Loading cover div
    "var c=document.createElement('div');c.id='llqt-cover';"
    "c.style.cssText='position:fixed;top:0;left:0;width:100vw;height:100vh;"
    "background:#1a1a2e;z-index:2147483647;display:flex;align-items:center;"
    "justify-content:center;color:#888;font-size:13px;font-family:sans-serif';"
    "c.textContent='Loading chat...';"
    "document.documentElement.appendChild(c);"
    # Make QWebEngine less detectable as an embedded browser.
    # TikTok's webcast API returns 403 for CSRF token when it detects
    # a non-standard browser (navigator.webdriver, missing window.chrome).
    "(function(){"
    "Object.defineProperty(navigator,'webdriver',{get:function(){return false}});"
    "if(!window.chrome){window.chrome={runtime:{},csi:function(){},loadTimes:function(){}}}"
    "Object.defineProperty(navigator,'plugins',{get:function(){"
    "return[{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer'},"
    "{name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai'},"
    "{name:'Native Client',filename:'internal-nacl-plugin'}];}});"
    "Object.defineProperty(navigator,'languages',{get:function(){"
    "return['en-US','en'];}});"
    "})();"
    "}"
)

# Isolate the TikTok LIVE chat panel, hiding the video player and other content.
#
# TikTok's live page DOM (rendered by React, not in SSR HTML):
#   BODY
#     <div id="__next"> or app root
#       MAIN
#         ...
#           .live-room-container  (main flex row)
#             [data-e2e="live-content-container"]  (video, left)
#             <div>  (chat panel wrapper, right)
#               [data-e2e="live-chat-container"]   (messages)
#               [data-e2e="live-chat-input-container"]  (input)
#
# Strategy: targeted hiding — hide the video container and page chrome
# (nav, header, footer), expand the chat panel to fill viewport. This
# avoids the aggressive path-based isolation that breaks React portals
# (emote picker, gift panels, tooltips rendered outside the chat tree).
_ISOLATE_CHAT_JS = """
(function() {
    try {
    var oldStyle = document.getElementById('llqt-chat-iso');
    if (oldStyle) oldStyle.remove();

    // Find the chat container via stable data-e2e attributes
    var chatInner = document.querySelector(
        '[data-e2e="live-chat-container"]');
    var chatEl = chatInner ? chatInner.parentElement : null;
    var matchedSelector = 'live-chat-container.parent';

    // Fallback selectors
    if (!chatEl || chatEl.offsetHeight < 30) {
        var fallbacks = [
            '[data-e2e="live-chat-input-container"]',
            '[class*="ChatContainer"]'
        ];
        for (var i = 0; i < fallbacks.length; i++) {
            var el = document.querySelector(fallbacks[i]);
            if (el && el.offsetHeight > 30) {
                chatEl = el.parentElement;
                matchedSelector = fallbacks[i] + '.parent';
                break;
            }
        }
    }

    if (!chatEl || chatEl.offsetHeight < 30) {
        var info = [];
        document.querySelectorAll('*').forEach(function(el) {
            var cls = (el.className || '').toString().toLowerCase();
            var e2e = el.getAttribute('data-e2e') || '';
            if ((cls.indexOf('chat') >= 0
                || e2e.indexOf('chat') >= 0) && el.offsetHeight > 10) {
                info.push(el.tagName
                    + (e2e ? '[e2e='+e2e+']' : '')
                    + ' [' + el.offsetWidth + 'x' + el.offsetHeight
                    + '] cls=' + cls.substring(0,60));
            }
        });
        return JSON.stringify(
            {status:'not_found', candidates:info.slice(0,20)});
    }

    // Find the layout container (.live-room-container) — parent of
    // both the video and chat panels.
    var layoutContainer = chatEl.parentElement;

    // Tag the video player for hiding
    var videoContainer = document.querySelector(
        '[data-e2e="live-content-container"]');
    if (videoContainer) {
        videoContainer.setAttribute('data-llqt-hide', '');
    }

    // Also hide siblings of the chat panel in the layout container
    // (anything that's not the chat panel — e.g. empty spacer divs)
    if (layoutContainer) {
        for (var i = 0; i < layoutContainer.children.length; i++) {
            var sib = layoutContainer.children[i];
            if (sib !== chatEl && !sib.hasAttribute('data-llqt-hide')) {
                sib.setAttribute('data-llqt-hide', '');
            }
        }
    }

    // Tag the chat panel
    chatEl.setAttribute('data-llqt-chat', '');

    // Build targeted CSS: hide video/nav/header, expand chat
    var style = document.createElement('style');
    style.id = 'llqt-chat-iso';
    var css = 'html,body{margin:0!important;padding:0!important;'
        + 'overflow:hidden!important;'
        + 'background:#1a1a2e!important}'
        // Hide tagged elements (video player, nav, etc.)
        + '[data-llqt-hide]{display:none!important}'
        // Hide common page chrome
        + 'header,nav,[class*="NavBar"],'
        + '[class*="Header"]:not([data-e2e*="chat"]),'
        + '[class*="footer"],[class*="Footer"],'
        + 'footer{display:none!important}'
        // Make chat panel fill the viewport
        + '[data-llqt-chat]{flex:1 1 100%!important;'
        + 'width:100%!important;height:100vh!important;'
        + 'max-width:none!important;max-height:none!important;'
        + 'border-radius:0!important;margin:0!important;'
        + 'border:none!important}';
    style.textContent = css;
    document.head.appendChild(style);

    // Make the chat messages area fill remaining space
    var kids = chatEl.children;
    var childInfo = [];
    for (var c = 0; c < kids.length; c++) {
        var kid = kids[c];
        childInfo.push({
            tag: kid.tagName,
            e2e: kid.getAttribute('data-e2e') || '',
            cls: (kid.className||'').toString().substring(0,60),
            w: kid.offsetWidth, h: kid.offsetHeight
        });
        if (kid.offsetHeight > 100) {
            kid.style.setProperty('flex', '1 1 0');
            kid.style.setProperty('overflow-y', 'auto');
            kid.style.setProperty('min-height', '0');
        } else {
            kid.style.setProperty('flex', '0 0 auto');
        }
    }

    // Force layout recalculation
    void chatEl.offsetHeight;
    setTimeout(function() {
        window.dispatchEvent(new Event('resize'));
    }, 50);
    setTimeout(function() {
        window.dispatchEvent(new Event('resize'));
    }, 500);

    // Remove the loading cover div
    var cover = document.getElementById('llqt-cover');
    if (cover) cover.remove();

    return JSON.stringify({status:'isolated', selector:matchedSelector,
        cls:(chatEl.className||'').toString().substring(0,80),
        children:childInfo});
    } catch(e) {
        return JSON.stringify({status:'error', message:e.toString()});
    }
})();
"""


class TikTokWebChatWidget(QWidget):
    """TikTok LIVE chat widget using an embedded web view.

    Loads the TikTok live page in a QWebEngineView and injects CSS/JS
    to isolate just the chat panel, hiding the video player and other content.
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
        self._isolation_attempts = 0

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
        if not profile:
            self._web_view = None
            self._loading_overlay = None
            placeholder = QLabel("TikTok embedded chat unavailable")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(placeholder, 1)
        else:
            from PySide6.QtGui import QColor
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
            from PySide6.QtWebEngineWidgets import QWebEngineView

            page = QWebEnginePage(profile, self)
            page.setBackgroundColor(QColor("#1a1a2e"))

            # Inject early-hide script at DocumentCreation
            hide_script = QWebEngineScript()
            hide_script.setName("tiktok-pre-isolate")
            hide_script.setWorldId(0)
            hide_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            hide_script.setSourceCode(_PRE_HIDE_JS)
            page.scripts().insert(hide_script)

            self._web_view = QWebEngineView(self)
            self._web_view.setPage(page)
            self._web_view.loadFinished.connect(self._on_load_finished)
            layout.addWidget(self._web_view, 1)

            # Native Qt overlay — prevents flash while DOM isolation runs
            theme = get_theme()
            self._loading_overlay = QLabel("Loading chat...", self)
            self._loading_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._loading_overlay.setAutoFillBackground(True)
            self._loading_overlay.setStyleSheet(
                f"background:{theme.chat_bg};color:{theme.text_muted};"
                "font-size:13px;font-family:sans-serif;"
            )
            self._loading_overlay.raise_()

            # Safety timeout: force-remove overlay after 30s
            self._isolation_timeout = QTimer(self)
            self._isolation_timeout.setSingleShot(True)
            self._isolation_timeout.setInterval(30_000)
            self._isolation_timeout.timeout.connect(self._on_isolation_timeout)

            # Navigate after layout is established
            QTimer.singleShot(200, self._navigate_to_chat)

        # Title refresh timer
        self._title_refresh_timer = QTimer(self)
        self._title_refresh_timer.setInterval(30_000)
        self._title_refresh_timer.timeout.connect(self._update_stream_title)

        self._update_banner_style()
        self._update_stream_title()
        if not self.settings.show_stream_title:
            self._title_banner.hide()
        if self.livestream and self.livestream.live:
            self._title_refresh_timer.start()

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Reposition the loading overlay to cover the web view area."""
        super().resizeEvent(event)
        if self._loading_overlay and self._web_view:
            self._loading_overlay.setGeometry(self._web_view.geometry())

    def _remove_loading_overlay(self) -> None:
        """Remove the loading overlay and stop the safety timeout."""
        if self._loading_overlay:
            self._loading_overlay.deleteLater()
            self._loading_overlay = None
        if hasattr(self, "_isolation_timeout"):
            self._isolation_timeout.stop()

    def _build_url(self) -> QUrl:
        """Build the TikTok LIVE URL."""
        return QUrl(f"https://www.tiktok.com/@{self.livestream.channel.channel_id}/live")

    def _navigate_to_chat(self) -> None:
        """Navigate to the TikTok LIVE URL after the widget is laid out."""
        if self._web_view:
            self._web_view.setUrl(self._build_url())
            if hasattr(self, "_isolation_timeout"):
                self._isolation_timeout.start()

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
            return True
        return super().eventFilter(obj, event)

    def _on_load_finished(self, ok: bool) -> None:
        """Handle page load completion."""
        if not self._web_view:
            return
        url = self._web_view.url().toString()
        if "tiktok.com" not in url:
            return
        if not ok:
            logger.warning(f"TikTok page failed to load for {self.channel_key}")
            return
        # Install event filter for Ctrl+scroll zoom on the Chromium render widget
        render_widget = self._web_view.focusProxy()
        if render_widget:
            render_widget.installEventFilter(self)
        # Wait for page to render, then isolate chat
        self._isolation_attempts = 0
        QTimer.singleShot(3000, self._try_isolate_chat)

    def _on_isolation_timeout(self) -> None:
        """Safety timeout: remove the loading overlay if isolation hasn't completed."""
        if self._loading_overlay:
            logger.warning("TikTok isolation timeout — removing loading overlay")
            self._remove_loading_overlay()

    def _try_isolate_chat(self) -> None:
        """Inject JS to find and isolate the chat panel."""
        if not self._web_view:
            return
        self._web_view.page().runJavaScript(_ISOLATE_CHAT_JS, 0, self._on_isolation_result)

    def _on_isolation_result(self, result) -> None:
        """Handle chat isolation result."""
        if result is None:
            logger.warning("TikTok chat isolation: JS returned None (script error?)")
            self._isolation_attempts += 1
            if self._isolation_attempts < 5:
                QTimer.singleShot(2000, self._try_isolate_chat)
            return

        if not isinstance(result, str):
            logger.warning(f"TikTok chat isolation: unexpected result type: {type(result)}")
            return

        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            logger.warning(f"TikTok chat isolation: invalid JSON: {result[:200]}")
            return

        if data.get("status") == "error":
            logger.warning(f"TikTok chat isolation JS error: {data.get('message')}")
            return

        if data.get("status") == "isolated":
            children = data.get("children", [])
            logger.info(
                f"TikTok chat isolated via '{data.get('selector')}' "
                f"(#{data.get('id')}.{data.get('cls', '')}) "
                f"children={children}"
            )
            self._remove_loading_overlay()
            return

        if data.get("status") == "not_found":
            candidates = data.get("candidates", [])
            self._isolation_attempts += 1
            logger.debug(
                f"TikTok chat not found (attempt {self._isolation_attempts}), "
                f"candidates: {candidates}"
            )
            if self._isolation_attempts < 5:
                QTimer.singleShot(2000, self._try_isolate_chat)
            else:
                logger.warning(
                    f"Failed to isolate TikTok chat after "
                    f"{self._isolation_attempts} attempts. "
                    f"Last candidates: {candidates}"
                )
                if self._web_view:
                    self._web_view.page().setHtml(
                        '<html><body style="background:#1a1a2e;color:#888;'
                        "display:flex;align-items:center;justify-content:center;"
                        "height:100vh;margin:0;font-family:sans-serif;"
                        'font-size:13px;text-align:center">'
                        "Chat not available<br>(stream may be offline or "
                        "chat panel not found)"
                        "</body></html>"
                    )
                self._remove_loading_overlay()

    # --- Banner methods ---

    def _update_banner_style(self) -> None:
        theme = get_theme()
        self._title_banner.applyBannerStyle(theme.chat_banner_bg, theme.chat_banner_text)
        self._socials_banner.applyBannerStyle(theme.chat_banner_bg, theme.chat_banner_text)

    def _update_stream_title(self) -> None:
        title = ""
        if self.livestream and self.livestream.title:
            title = self.livestream.title

        if title and self.settings.show_stream_title and not self._title_dismissed:
            escaped = html.escape(title)
            html_title = _COMMAND_RE.sub(r"<b>\1</b>", escaped)

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
        self._title_dismissed = True
        self._title_refresh_timer.stop()

    def _on_socials_dismissed(self) -> None:
        self._socials_dismissed = True

    # --- Public interface (called by ChatWindow) ---

    def apply_theme(self) -> None:
        self._update_banner_style()

    def update_banner_settings(self) -> None:
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
        self.livestream = livestream
        self._update_stream_title()
        if livestream and livestream.live:
            self._title_refresh_timer.start()
        else:
            self._title_refresh_timer.stop()

    def set_socials(self, socials: dict) -> None:
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
        self._title_refresh_timer.stop()
        self._remove_loading_overlay()
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
