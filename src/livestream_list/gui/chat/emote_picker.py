"""Emote picker popup with searchable grid."""

import logging

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...chat.emotes.cache import DOWNLOAD_PRIORITY_HIGH, DOWNLOAD_PRIORITY_LOW, EmoteCache
from ...chat.models import ChatEmote
from ..theme import get_theme

logger = logging.getLogger(__name__)

EMOTE_BUTTON_SIZE = 36
GRID_COLUMNS = 8
_SCAN_BATCH_SIZE = 50  # Buttons per tick for deferred animation/download scan

# Tab ordering: platform emotes first, then 3rd party alphabetically
_PROVIDER_ORDER = {
    "twitch": 0,
    "kick": 1,
    "7tv": 2,
    "bttv": 3,
    "ffz": 4,
}

_PROVIDER_NAMES = {
    "twitch": "Twitch",
    "kick": "Kick",
    "7tv": "7TV",
    "bttv": "BTTV",
    "ffz": "FFZ",
}


class EmotePickerWidget(QWidget):
    """Searchable emote picker popup.

    Shows emotes in a grid organized by provider tabs.
    Clicking an emote inserts its code at the cursor position.
    """

    emote_selected = Signal(str)  # emote name/code

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._emotes: dict[str, list[ChatEmote]] = {}  # provider -> emotes
        self._channel_emote_names: set[str] = set()
        self._locked_emote_names: set[str] = set()  # Sub-only emotes user can't use
        self._image_store: EmoteCache | None = None
        self._all_buttons: list[tuple[QPushButton, ChatEmote]] = []
        self._animated_buttons: list[tuple[QPushButton, ChatEmote, str]] = []  # btn, emote, key
        self._btn_style: str = ""  # Cached button stylesheet
        self._needs_rebuild: bool = False
        self._pending_tabs: list[tuple[str, list[ChatEmote]]] = []
        self._build_timer = QTimer(self)
        self._build_timer.setSingleShot(True)
        self._build_timer.setInterval(0)
        self._build_timer.timeout.connect(self._build_next_tab)
        self._gif_timer = None
        self._gif_connected: bool = False
        self._animation_time_ms: int = 0
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(0)
        self._scan_timer.timeout.connect(self._process_scan_batch)
        self._scan_index: int = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the picker UI."""
        self.setWindowFlags(Qt.WindowType.Popup)
        self.resize(320, 350)
        self.setMinimumSize(200, 200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Search bar + filter row
        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search emotes...")
        self._search.textChanged.connect(self._apply_filters)
        search_row.addWidget(self._search)

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["All", "Animated", "Static"])
        self._filter_combo.setFixedWidth(90)
        self._filter_combo.currentIndexChanged.connect(lambda _: self._apply_filters())
        search_row.addWidget(self._filter_combo)

        layout.addLayout(search_row)

        # Tab widget for providers
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # Apply theme styling
        self.apply_theme()

    def apply_theme(self) -> None:
        """Apply theme colors to the picker."""
        theme = get_theme()
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background-color: {theme.chat_input_bg};
                border: 1px solid {theme.border_light};
                border-radius: 4px;
                padding: 4px 8px;
                color: {theme.text_primary};
                font-size: 12px;
            }}
        """)
        self._filter_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {theme.chat_input_bg};
                border: 1px solid {theme.border_light};
                border-radius: 4px;
                padding: 4px 6px;
                color: {theme.text_primary};
                font-size: 12px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 16px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {theme.popup_bg};
                color: {theme.text_primary};
                selection-background-color: {theme.accent};
                border: 1px solid {theme.border_light};
            }}
        """)
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background-color: {theme.popup_bg};
            }}
            QTabBar::tab {{
                background-color: {theme.chat_input_bg};
                color: {theme.text_muted};
                padding: 4px 8px;
                font-size: 11px;
                border: none;
            }}
            QTabBar::tab:selected {{
                color: {theme.selection_text};
                border-bottom: 2px solid {theme.accent};
            }}
        """)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {theme.popup_bg};
                border: 1px solid {theme.border_light};
                border-radius: 6px;
            }}
        """)
        # Cache button style so we don't regenerate per-button
        self._btn_style = f"""
            QPushButton {{
                font-size: 8px;
                color: {theme.text_muted};
                background-color: {theme.chat_input_bg};
                border: 1px solid transparent;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                border-color: {theme.accent};
                background-color: {theme.popup_hover};
            }}
        """

    def set_emotes(
        self,
        emotes_by_provider: dict[str, list[ChatEmote]],
        channel_emote_names: set[str] | None = None,
        locked_emote_names: set[str] | None = None,
    ) -> None:
        """Set the available emotes, organized by provider."""
        if self._emotes is emotes_by_provider:
            return
        self._emotes = emotes_by_provider
        self._channel_emote_names = channel_emote_names or set()
        self._locked_emote_names = locked_emote_names or set()
        # Don't rebuild while visible — just refresh icons to avoid
        # destroying buttons mid-iteration (causes SIGSEGV)
        if self.isVisible():
            self._needs_rebuild = True
            return
        self._rebuild_tabs()

    def refresh_icons(self) -> None:
        """Update button icons for emotes whose images have loaded since creation.

        Delegates to the deferred batched scan to avoid blocking the main thread.
        """
        self._start_deferred_scan()

    def set_image_store(self, store: EmoteCache) -> None:
        """Set the shared image store."""
        self._image_store = store

    def set_gif_timer(self, timer) -> None:
        """Set the shared GIF timer for animating emotes."""
        self._gif_timer = timer

    def _current_scale(self) -> float:
        try:
            return float(self.devicePixelRatioF())
        except Exception:
            return 1.0

    def show_picker(self, pos) -> None:
        """Show the picker at the given position."""
        if self._needs_rebuild:
            self._needs_rebuild = False
            self._rebuild_tabs()
        self.move(pos)
        self._search.clear()
        self._filter_combo.setCurrentIndex(0)
        self._search.setFocus()
        self.show()
        # Kick off deferred scan (animation detection + missing emote requests)
        self._start_deferred_scan()

    def hideEvent(self, event) -> None:  # noqa: N802
        """Disconnect animation timer when picker closes."""
        self._scan_timer.stop()
        if self._gif_timer and self._gif_connected:
            try:
                self._gif_timer.tick.disconnect(self._on_gif_tick)
            except Exception:
                pass
            self._gif_connected = False
        super().hideEvent(event)

    def _sorted_providers(self) -> list[tuple[str, list[ChatEmote]]]:
        """Return providers sorted: platform first, then 3rd party alphabetically."""
        return sorted(
            ((p, e) for p, e in self._emotes.items() if e),
            key=lambda x: (_PROVIDER_ORDER.get(x[0], 99), x[0]),
        )

    def _rebuild_tabs(self) -> None:
        """Rebuild the tab widget with current emotes.

        Builds the first tab immediately for fast display, then defers
        remaining tabs via timer to keep the UI responsive.
        """
        # Disconnect gif timer before destroying buttons to prevent
        # _on_gif_tick from accessing stale C++ button pointers (SIGSEGV)
        if self._gif_timer and self._gif_connected:
            try:
                self._gif_timer.tick.disconnect(self._on_gif_tick)
            except Exception:
                pass
            self._gif_connected = False
        self._tabs.clear()
        self._all_buttons.clear()
        self._animated_buttons.clear()
        self._build_timer.stop()
        self._scan_timer.stop()

        providers = self._sorted_providers()
        if not providers:
            return

        # Build the first tab immediately so the picker isn't empty
        provider, emotes = providers[0]
        self._build_tab(provider, emotes)

        # Defer remaining tabs
        self._pending_tabs = providers[1:]
        if self._pending_tabs:
            self._build_timer.start()

    def _build_next_tab(self) -> None:
        """Build the next deferred tab."""
        if not self._pending_tabs:
            return
        provider, emotes = self._pending_tabs.pop(0)
        self._build_tab(provider, emotes)
        if self._pending_tabs:
            self._build_timer.start()

    def _build_tab(self, provider: str, emotes: list[ChatEmote]) -> None:
        """Build a single provider tab with emote buttons."""
        theme = get_theme()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(2)
        layout.setContentsMargins(2, 2, 2, 2)

        # Split into channel and global emotes
        channel_emotes = [e for e in emotes if e.name in self._channel_emote_names]
        global_emotes = [e for e in emotes if e.name not in self._channel_emote_names]

        scale = self._current_scale()
        icon_size = QSize(EMOTE_BUTTON_SIZE - 4, EMOTE_BUTTON_SIZE - 4)

        if channel_emotes:
            chan_label = QLabel("Channel")
            chan_label.setStyleSheet(
                f"color: {theme.text_muted}; font-size: 10px;"
                " background: transparent; border: none; padding: 2px 0;"
            )
            layout.addWidget(chan_label)
            chan_grid = QGridLayout()
            chan_grid.setSpacing(2)
            for i, emote in enumerate(channel_emotes):
                btn = self._make_button(emote, scale, icon_size)
                chan_grid.addWidget(btn, i // GRID_COLUMNS, i % GRID_COLUMNS)
                self._all_buttons.append((btn, emote))
            layout.addLayout(chan_grid)

            # Separator line
            if global_emotes:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet(f"color: {theme.border_light}; background: transparent;")
                sep.setFixedHeight(1)
                layout.addWidget(sep)

        if global_emotes:
            if channel_emotes:
                glob_label = QLabel("Global")
                glob_label.setStyleSheet(
                    f"color: {theme.text_muted}; font-size: 10px;"
                    " background: transparent; border: none; padding: 2px 0;"
                )
                layout.addWidget(glob_label)
            glob_grid = QGridLayout()
            glob_grid.setSpacing(2)
            for i, emote in enumerate(global_emotes):
                btn = self._make_button(emote, scale, icon_size)
                glob_grid.addWidget(btn, i // GRID_COLUMNS, i % GRID_COLUMNS)
                self._all_buttons.append((btn, emote))
            layout.addLayout(glob_grid)

        scroll.setWidget(container)
        tab_name = _PROVIDER_NAMES.get(provider, provider)
        self._tabs.addTab(scroll, tab_name)

    def _make_button(self, emote: ChatEmote, scale: float, icon_size: QSize) -> QPushButton:
        """Create a single emote button."""
        locked = emote.name in self._locked_emote_names
        btn = QPushButton()
        btn.setFixedSize(EMOTE_BUTTON_SIZE, EMOTE_BUTTON_SIZE)
        btn.setStyleSheet(self._btn_style)

        if locked:
            btn.setToolTip(f"{emote.name}\nSubscribe to use")
            btn.setEnabled(False)
            opacity = QGraphicsOpacityEffect(btn)
            opacity.setOpacity(0.35)
            btn.setGraphicsEffect(opacity)
        else:
            btn.setToolTip(emote.name)

        pixmap = self._get_emote_pixmap(emote, scale)
        if pixmap:
            btn.setIcon(QIcon(pixmap))
            btn.setIconSize(icon_size)
        else:
            btn.setText(emote.name[:3])

        if not locked:
            btn.clicked.connect(lambda checked=False, name=emote.name: self._on_emote_clicked(name))
        return btn

    def _get_emote_pixmap(self, emote: ChatEmote, scale: float) -> QPixmap | None:
        """Try to get a pixmap for an emote from cache. Returns None if not ready."""
        if not self._image_store or not emote.image_set:
            return None
        image_set = emote.image_set.bind(self._image_store)
        emote.image_set = image_set
        image_ref = image_set.get_image_or_loaded(scale=scale)
        if image_ref:
            pixmap = image_ref.pixmap_or_load()
            if pixmap and not pixmap.isNull():
                return pixmap
        return None

    def _on_emote_clicked(self, name: str) -> None:
        """Handle emote button click."""
        self.emote_selected.emit(name)
        self.hide()

    def _apply_filters(self, _text: str | None = None) -> None:
        """Filter emotes by search text and type filter."""
        search = self._search.text().lower()
        filter_idx = self._filter_combo.currentIndex()  # 0=All, 1=Animated, 2=Static
        cache = self._image_store
        scale = self._current_scale()

        for btn, emote in self._all_buttons:
            # Text filter
            if search and search not in emote.name.lower():
                btn.setVisible(False)
                continue

            # Type filter
            if filter_idx != 0 and cache and emote.image_set:
                image_set = emote.image_set.bind(cache)
                emote.image_set = image_set
                image_ref = image_set.get_image_or_loaded(scale=scale)
                if image_ref:
                    is_animated = cache.is_emote_animated(image_ref.key)
                    if is_animated is not None:
                        if filter_idx == 1 and not is_animated:
                            btn.setVisible(False)
                            continue
                        if filter_idx == 2 and is_animated:
                            btn.setVisible(False)
                            continue

            btn.setVisible(True)

    def _start_deferred_scan(self) -> None:
        """Begin batched scan of buttons for animation data and missing downloads."""
        self._animated_buttons.clear()
        self._scan_index = 0
        self._scan_timer.start()

    def _process_scan_batch(self) -> None:
        """Process a batch of buttons — set icons, detect animation, request downloads.

        Combines the work of the old refresh_icons, _detect_animated_buttons, and
        _request_missing_emotes into one batched pass. Processes _SCAN_BATCH_SIZE
        buttons per event loop tick to avoid blocking the main thread.
        """
        if not self._image_store or not self._all_buttons:
            return
        cache = self._image_store
        scale = self._current_scale()
        icon_size = QSize(EMOTE_BUTTON_SIZE - 4, EMOTE_BUTTON_SIZE - 4)
        end = min(self._scan_index + _SCAN_BATCH_SIZE, len(self._all_buttons))

        for i in range(self._scan_index, end):
            btn, emote = self._all_buttons[i]
            if not emote.image_set:
                continue
            image_set = emote.image_set.bind(cache)
            emote.image_set = image_set

            try:
                has_icon = btn.icon() and not btn.icon().isNull()
            except RuntimeError:
                continue

            image_ref = image_set.get_image_or_loaded(scale=scale)

            if image_ref:
                key = image_ref.key
                # Set icon from cache if button is still blank
                if not has_icon:
                    pixmap = image_ref.pixmap_or_load()
                    if pixmap and not pixmap.isNull():
                        try:
                            btn.setIcon(QIcon(pixmap))
                            btn.setIconSize(icon_size)
                            btn.setText("")
                            has_icon = True
                        except RuntimeError:
                            continue

                # Animation detection
                if cache.has_animation_data(key):
                    if key not in cache.animated_dict:
                        cache.request_animation_frames(key)
                    self._animated_buttons.append((btn, emote, key))

            # Request download for emotes still without icons
            if not has_icon:
                is_channel = emote.name in self._channel_emote_names
                priority = DOWNLOAD_PRIORITY_HIGH if is_channel else DOWNLOAD_PRIORITY_LOW
                image_set.prefetch(scale=scale, priority=priority)

        self._scan_index = end

        if self._scan_index < len(self._all_buttons):
            self._scan_timer.start()
        else:
            # Scan complete — connect gif timer if animated buttons were found
            if self._gif_timer and self._animated_buttons and not self._gif_connected:
                self._gif_timer.tick.connect(self._on_gif_tick)
                self._gif_connected = True

    def _on_gif_tick(self, elapsed_ms: int) -> None:
        """Advance animation frame for visible animated emote buttons."""
        if not self._image_store or not self._animated_buttons:
            return
        self._animation_time_ms = elapsed_ms
        cache = self._image_store
        # Find the scroll area for the active tab
        scroll = self._tabs.currentWidget()
        if not scroll or not hasattr(scroll, "viewport"):
            return
        viewport = scroll.viewport()
        if not viewport:
            return
        vp_rect = viewport.rect()
        icon_size = QSize(EMOTE_BUTTON_SIZE - 4, EMOTE_BUTTON_SIZE - 4)
        for btn, _emote, key in self._animated_buttons:
            try:
                if not btn.isVisible():
                    continue
                # Check if button is within the scroll viewport
                btn_pos = btn.mapTo(viewport, btn.rect().topLeft())
                if not vp_rect.intersects(btn.rect().translated(btn_pos)):
                    continue
            except RuntimeError:
                # Button's C++ object was destroyed — skip it
                continue
            frames = cache.get_frames(key)
            if not frames:
                continue
            frame = self._pick_frame(frames, key)
            if frame:
                try:
                    btn.setIcon(QIcon(frame))
                    btn.setIconSize(icon_size)
                    btn.setText("")
                except RuntimeError:
                    continue

    def _pick_frame(self, frames: list[QPixmap], key: str) -> QPixmap | None:
        """Select the correct animation frame based on per-frame delays."""
        if not frames:
            return None
        delays = self._image_store.get_frame_delays(key) if self._image_store else None
        if delays and len(delays) == len(frames):
            total = sum(delays)
            if total > 0:
                t = self._animation_time_ms % total
                idx = 0
                for delay in delays:
                    if t < delay:
                        break
                    t -= delay
                    idx += 1
                return frames[idx % len(frames)]
        idx = (self._animation_time_ms // 50) % len(frames)
        return frames[idx]
