"""Chaturbate chat widget using embedded QWebEngineView."""

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

# Pattern for !commands in stream titles
_COMMAND_RE = re.compile(r"(![a-zA-Z]\w*)")

# QWebEngine imports are deferred to avoid crashing Flatpak builds
_chaturbate_webengine_available: bool | None = None

# Shared persistent profile (singleton) — separate from YouTube's
_chaturbate_profile: QWebEngineProfile | None = None

# Persistent cookie tracker for Chaturbate
_tracked_cookies: dict[str, str] = {}  # name -> value
_tracker_connected: bool = False
_force_logged_out: bool = False


def _ensure_webengine() -> bool:
    """Lazily import QWebEngine modules. Returns True if available."""
    global _chaturbate_webengine_available
    if _chaturbate_webengine_available is not None:
        return _chaturbate_webengine_available
    try:
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile  # noqa: F401
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

        _chaturbate_webengine_available = True
    except ImportError:
        logger.warning("QWebEngine not available — Chaturbate embedded chat disabled")
        _chaturbate_webengine_available = False
    return _chaturbate_webengine_available


def _on_cookie_added(cookie) -> None:
    """Track Chaturbate cookies as they're added to the profile."""
    global _force_logged_out
    domain = cookie.domain()
    name = cookie.name().data().decode()
    if "chaturbate.com" in domain:
        value = cookie.value().data().decode()
        _tracked_cookies[name] = value
        if name == "sessionid":
            _force_logged_out = False


def _on_cookie_removed(cookie) -> None:
    """Track Chaturbate cookie removals.

    We intentionally do NOT remove from _tracked_cookies here.
    QWebEngine fires cookieRemoved when rotating/refreshing cookies
    (remove old → add new), which would cause the API client to lose
    cookies between refresh cycles. Cookies are only cleared on
    explicit logout via clear_chaturbate_cookies().
    """
    pass


def _get_shared_profile() -> QWebEngineProfile | None:
    """Get or create the shared persistent QWebEngineProfile for Chaturbate.

    Returns None if QWebEngine is not available.
    """
    global _chaturbate_profile, _tracker_connected
    if not _ensure_webengine():
        return None

    from PySide6.QtWebEngineCore import QWebEngineProfile

    if _chaturbate_profile is None:
        storage_path = str(get_data_dir() / "webengine_chaturbate")
        _chaturbate_profile = QWebEngineProfile("chaturbate_chat")
        _chaturbate_profile.setCachePath(storage_path)
        _chaturbate_profile.setPersistentStoragePath(storage_path)

    if not _tracker_connected:
        _tracker_connected = True
        store = _chaturbate_profile.cookieStore()
        store.cookieAdded.connect(_on_cookie_added)
        store.cookieRemoved.connect(_on_cookie_removed)
        store.loadAllCookies()

    return _chaturbate_profile


def _read_cookies_from_db() -> dict[str, str]:
    """Read Chaturbate cookies directly from the Chromium SQLite database."""
    import sqlite3

    db_path = Path(get_data_dir() / "webengine_chaturbate" / "Cookies")
    if not db_path.exists():
        return {}

    cookies: dict[str, str] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.execute(
            "SELECT name, value FROM cookies WHERE host_key LIKE '%chaturbate.com'"
        )
        for name, value in cursor:
            cookies[name] = value
        conn.close()
    except Exception as e:
        logger.debug(f"Could not read Chaturbate cookie DB: {e}")
    return cookies


def _get_all_cookies() -> dict[str, str]:
    """Get Chaturbate cookies from all sources."""
    cookies = _read_cookies_from_db()
    cookies.update(_tracked_cookies)
    return cookies


def get_chaturbate_cookie_string() -> str:
    """Extract Chaturbate cookies as 'name=value; ...' string."""
    cookies = _get_all_cookies()
    if not cookies:
        return ""
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def has_chaturbate_login() -> bool:
    """Check if the Chaturbate profile has auth cookies (sessionid present)."""
    if _force_logged_out:
        return False
    if "sessionid" in _tracked_cookies:
        return True
    cookies = _read_cookies_from_db()
    return "sessionid" in cookies


def clear_chaturbate_cookies() -> None:
    """Clear all cookies from the shared Chaturbate profile."""
    global _force_logged_out
    profile = _get_shared_profile()
    if profile is not None:
        profile.cookieStore().deleteAllCookies()
    _tracked_cookies.clear()
    _force_logged_out = True


# Dismiss Chaturbate's age verification overlay
_DISMISS_AGE_GATE_JS = """
(function() {
    var btn = document.getElementById('close_entrance_terms');
    if (btn) { btn.click(); return 'clicked'; }
    var overlay = document.getElementById('entrance_terms_overlay');
    if (overlay && overlay.style.display !== 'none') {
        overlay.style.display = 'none';
        overlay.style.visibility = 'hidden';
        return 'force_hidden';
    }
    return 'no_gate';
})();
"""

# Find and isolate the chat panel, hiding everything else on the page.
# The chat container (#ChatTabContainer or #DraggableChatTabContainer)
# includes CHAT, PM, USERS, and SETTINGS tabs natively.
_ISOLATE_CHAT_JS = """
(function() {
    try {
    var oldStyle = document.getElementById('llqt-chat-iso');
    if (oldStyle) oldStyle.remove();

    // Priority: sidebar chat (always-visible, not the video-hover overlay).
    // These may be hidden in theater/split mode — we force them visible.
    var prioritySelectors = ['#ChatTabContainer', '#defchat'];
    var fallbackSelectors = ['.chat-holder', '#chat-box', '.chat-container'];

    var chatEl = null;
    var matchedSelector = '';

    // Try priority selectors first — use even if hidden (zero height)
    for (var i = 0; i < prioritySelectors.length; i++) {
        chatEl = document.querySelector(prioritySelectors[i]);
        if (chatEl) {
            matchedSelector = prioritySelectors[i];
            break;
        }
        chatEl = null;
    }

    // Fallback selectors (require minimum height)
    if (!chatEl) {
        for (var i = 0; i < fallbackSelectors.length; i++) {
            chatEl = document.querySelector(fallbackSelectors[i]);
            if (chatEl && chatEl.offsetHeight > 50) {
                matchedSelector = fallbackSelectors[i];
                break;
            }
            chatEl = null;
        }
    }

    if (!chatEl) {
        var info = [];
        document.querySelectorAll('*').forEach(function(el) {
            var cls = (el.className || '').toString().toLowerCase();
            var elId = (el.id || '').toLowerCase();
            if ((cls.indexOf('chat') >= 0 || elId.indexOf('chat') >= 0)
                && el.offsetHeight > 10) {
                info.push(el.tagName + (el.id ? '#'+el.id : '')
                    + ' [' + el.offsetWidth + 'x' + el.offsetHeight + ']');
            }
        });
        return JSON.stringify({status:'not_found', candidates:info.slice(0,20)});
    }

    var ancestor = chatEl.parentElement;
    while (ancestor && ancestor !== document.documentElement) {
        ancestor.setAttribute('data-llqt-path', '');
        ancestor = ancestor.parentElement;
    }
    chatEl.setAttribute('data-llqt-chat', '');

    var style = document.createElement('style');
    style.id = 'llqt-chat-iso';
    style.textContent =
        'html,body{margin:0!important;padding:0!important;overflow:hidden!important;background:#1a1a2e!important}'
        + 'body>*:not([data-llqt-path]):not([data-llqt-chat]):not([data-llqt-popup]){display:none!important}'
        + '[data-llqt-path]>*:not([data-llqt-path]):not([data-llqt-chat]):not([data-llqt-popup]){display:none!important}'
        + '[data-llqt-path]{display:block!important;position:static!important;margin:0!important;padding:0!important;width:100%!important;height:100%!important;max-width:none!important;max-height:none!important;overflow:visible!important;float:none!important;flex:1 1 100%!important}'
        + '[data-llqt-chat]{display:flex!important;flex-direction:column!important;position:fixed!important;top:0!important;left:0!important;width:100vw!important;height:100vh!important;max-width:none!important;max-height:none!important;z-index:999999!important}';
    document.head.appendChild(style);

    // Watch for dynamically added popups/overlays (settings panels, modals)
    // that Chaturbate renders at body level outside the chat container.
    new MutationObserver(function(mutations) {
        mutations.forEach(function(m) {
            m.addedNodes.forEach(function(n) {
                if (n.nodeType === 1 && n.id !== 'llqt-chat-iso'
                    && n.id !== 'llqt-cover') {
                    n.setAttribute('data-llqt-popup', '');
                }
            });
        });
    }).observe(document.body, {childList: true});

    // Dynamically fix the chat content area to fill remaining space.
    var kids = chatEl.children;
    var childInfo = [];
    for (var c = 0; c < kids.length; c++) {
        var kid = kids[c];
        childInfo.push({
            tag: kid.tagName, id: kid.id || '',
            cls: (kid.className||'').toString().substring(0,60),
            w: kid.offsetWidth, h: kid.offsetHeight
        });
        if (kid.id !== 'tab-row' && kid.offsetHeight > 50) {
            kid.style.setProperty('flex', '1 1 0');
            kid.style.setProperty('overflow-y', 'auto');
            kid.style.setProperty('min-height', '0');
        } else {
            kid.style.setProperty('flex', '0 0 auto');
        }
    }

    // Force browser to recalculate layout
    void chatEl.offsetHeight;
    setTimeout(function() { window.dispatchEvent(new Event('resize')); }, 50);
    setTimeout(function() { window.dispatchEvent(new Event('resize')); }, 500);

    // Tab-switching helper: Chaturbate's JS shows the target panel but may
    // not hide the other .window children, causing them to stack/overlap.
    // We add listeners that ensure only one child is visible at a time.
    var win = chatEl.querySelector('.window');
    if (win) {
        var tabIds = ['chat-tab-default', 'pm-tab-default',
                      'users-tab-default', 'settings-tab-default'];
        tabIds.forEach(function(tabId, tabIndex) {
            var tab = document.getElementById(tabId);
            if (!tab) return;
            tab.addEventListener('click', function() {
                setTimeout(function() {
                    for (var j = 0; j < win.children.length; j++) {
                        var child = win.children[j];
                        var show = false;
                        if (tabId === 'chat-tab-default') show = (j === 0);
                        else if (tabId === 'pm-tab-default') show = (j === 1);
                        else if (tabId === 'users-tab-default')
                            show = (child.id === 'UserListTab');
                        else if (tabId === 'settings-tab-default')
                            show = (child.id === 'SettingsTab'
                                || (child.className||'').toString().indexOf('settings') >= 0
                                || j === win.children.length - 1);
                        child.style.display = show ? 'block' : 'none';
                    }
                }, 150);
            });
        });
    }

    // Remove the loading cover div now that isolation is complete
    var cover = document.getElementById('llqt-cover');
    if (cover) cover.remove();

    // Chaturbate's JS intercepts keyboard events globally (keydown/keypress
    // listeners on document/window) which prevents typing in the contenteditable.
    // We add capture-phase listeners on both document AND window that stop
    // propagation when the chat input has focus, plus beforeinput to prevent
    // Chaturbate from blocking text insertion.
    var chatInputSelector = '.ChatTabContents [contenteditable="true"]';
    function isChatInputFocused() {
        var activeEl = document.activeElement;
        var chatInput = chatEl.querySelector(chatInputSelector);
        return chatInput && (activeEl === chatInput || chatInput.contains(activeEl));
    }
    var evtTypes = ['keydown', 'keypress', 'keyup', 'beforeinput', 'textInput'];
    [document, window].forEach(function(target) {
        evtTypes.forEach(function(evtType) {
            target.addEventListener(evtType, function(e) {
                if (isChatInputFocused()) {
                    // Let Enter through — Chaturbate's JS needs it to send messages
                    if (e.key === 'Enter') return;
                    e.stopImmediatePropagation();
                }
            }, true);  // capture phase
        });
    });

    return JSON.stringify({status:'isolated', selector:matchedSelector,
        id:chatEl.id||'', cls:(chatEl.className||'').toString().substring(0,80),
        children:childInfo});
    } catch(e) {
        return JSON.stringify({status:'error', message:e.toString()});
    }
})();
"""


class ChaturbateWebChatWidget(QWidget):
    """Chaturbate chat widget using an embedded web view.

    Loads the Chaturbate room page in a QWebEngineView and injects CSS/JS
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
            placeholder = QLabel("Chaturbate embedded chat unavailable")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(placeholder, 1)
        else:
            from PySide6.QtGui import QColor
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
            from PySide6.QtWebEngineWidgets import QWebEngineView

            page = QWebEnginePage(profile, self)
            page.setBackgroundColor(QColor("#1a1a2e"))

            # Inject early-hide script as secondary fallback: hides page content
            # at DocumentCreation in case the overlay removal is slow.
            hide_script = QWebEngineScript()
            hide_script.setName("chaturbate-pre-isolate")
            hide_script.setWorldId(0)
            hide_script.setInjectionPoint(
                QWebEngineScript.InjectionPoint.DocumentCreation
            )
            hide_script.setSourceCode(
                "if(location.hostname&&location.hostname.indexOf('chaturbate.com')>=0){"
                "var c=document.createElement('div');c.id='llqt-cover';"
                "c.style.cssText='position:fixed;top:0;left:0;width:100vw;height:100vh;"
                "background:#1a1a2e;z-index:2147483647;display:flex;align-items:center;"
                "justify-content:center;color:#888;font-size:13px;font-family:sans-serif';"
                "c.textContent='Loading chat...';"
                "document.documentElement.appendChild(c);"
                "}"
            )
            page.scripts().insert(hide_script)

            self._web_view = QWebEngineView(self)
            self._web_view.setPage(page)
            self._web_view.loadFinished.connect(self._on_load_finished)
            layout.addWidget(self._web_view, 1)

            # Native Qt overlay on top of the web view — prevents the full-page
            # flash while keeping the web view visible (Chromium needs visibility
            # to render the DOM properly for isolation JS).
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

            # Navigate to real URL after layout is established
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
        """Build the Chaturbate room URL."""
        return QUrl(f"https://chaturbate.com/{self.livestream.channel.channel_id}")

    def _navigate_to_chat(self) -> None:
        """Navigate to the real Chaturbate URL after the widget is laid out."""
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
        if "chaturbate.com" not in url:
            return  # Ignore loads of placeholder / error pages
        if not ok:
            logger.warning(f"Chaturbate page failed to load for {self.channel_key}")
            return
        # Install event filter for Ctrl+scroll zoom on the Chromium render widget
        render_widget = self._web_view.focusProxy()
        if render_widget:
            render_widget.installEventFilter(self)
        # Start isolation: dismiss age gate first
        self._isolation_attempts = 0
        self._web_view.page().runJavaScript(
            _DISMISS_AGE_GATE_JS, 0, self._on_age_gate_result
        )

    def _on_age_gate_result(self, result) -> None:
        """Handle age gate dismissal, then isolate chat."""
        logger.debug(f"Chaturbate age gate: {result}")
        # Wait for page to settle after age gate dismissal
        delay = 3000 if result in ("clicked", "force_hidden") else 1500
        QTimer.singleShot(delay, self._try_isolate_chat)

    def _on_isolation_timeout(self) -> None:
        """Safety timeout: remove the loading overlay if isolation hasn't completed."""
        if self._loading_overlay:
            logger.warning("Chaturbate isolation timeout — removing loading overlay")
            self._remove_loading_overlay()

    def _try_isolate_chat(self) -> None:
        """Inject JS to find and isolate the chat panel."""
        if not self._web_view:
            return
        self._web_view.page().runJavaScript(
            _ISOLATE_CHAT_JS, 0, self._on_isolation_result
        )

    def _on_isolation_result(self, result) -> None:
        """Handle chat isolation result."""
        if result is None:
            logger.warning("Chat isolation: JS returned None (script error?)")
            self._isolation_attempts += 1
            if self._isolation_attempts < 5:
                QTimer.singleShot(2000, self._try_isolate_chat)
            return

        if not isinstance(result, str):
            logger.warning(f"Chat isolation: unexpected result type: {type(result)}")
            return

        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            logger.warning(f"Chat isolation: invalid JSON: {result[:200]}")
            return

        if data.get("status") == "error":
            logger.warning(f"Chat isolation JS error: {data.get('message')}")
            return

        if data.get("status") == "isolated":
            children = data.get("children", [])
            logger.info(
                f"Chaturbate chat isolated via '{data.get('selector')}' "
                f"(#{data.get('id')}.{data.get('cls', '')}) "
                f"children={children}"
            )
            self._remove_loading_overlay()
            return

        if data.get("status") == "not_found":
            candidates = data.get("candidates", [])
            self._isolation_attempts += 1
            logger.debug(
                f"Chat not found (attempt {self._isolation_attempts}), "
                f"candidates: {candidates}"
            )
            if self._isolation_attempts < 5:
                QTimer.singleShot(2000, self._try_isolate_chat)
            else:
                logger.warning(
                    f"Failed to isolate Chaturbate chat after "
                    f"{self._isolation_attempts} attempts. "
                    f"Last candidates: {candidates}"
                )
                if self._web_view:
                    self._web_view.page().setHtml(
                        '<html><body style="background:#1a1a2e;color:#888;'
                        'display:flex;align-items:center;justify-content:center;'
                        'height:100vh;margin:0;font-family:sans-serif;'
                        'font-size:13px;text-align:center">'
                        'Chat not available<br>(room may be offline)'
                        '</body></html>'
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
                html_title += (
                    f'<br><span style="font-size: 10px; opacity: 0.7;">{meta_html}</span>'
                )

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
