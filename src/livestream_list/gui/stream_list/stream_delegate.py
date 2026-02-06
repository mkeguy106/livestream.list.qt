"""Custom delegate for painting stream rows in a virtualized QListView."""

import logging
import time

from PySide6.QtCore import QEvent, QLineF, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from ...core.models import Livestream, StreamPlatform, UIStyle
from ..theme import get_theme
from .stream_model import PlayingRole, SelectionRole, StreamRole

_delegate_logger = logging.getLogger(__name__)

# Platform colors
PLATFORM_COLORS = {
    StreamPlatform.TWITCH: "#9146FF",
    StreamPlatform.KICK: "#53FC18",
    StreamPlatform.YOUTUBE: "#FF0000",
}

# Platform icon text
PLATFORM_ICONS = {
    StreamPlatform.TWITCH: "T",
    StreamPlatform.YOUTUBE: "Y",
    StreamPlatform.KICK: "K",
}

# UI style configurations
UI_STYLES = {
    UIStyle.DEFAULT: {
        "margin_v": 4,
        "margin_h": 12,
        "spacing": 10,
        "icon_size": 16,
        "show_title": True,
    },
    UIStyle.COMPACT_1: {
        "margin_v": 4,
        "margin_h": 12,
        "spacing": 8,
        "icon_size": 14,
        "show_title": False,
    },
    UIStyle.COMPACT_2: {
        "margin_v": 2,
        "margin_h": 6,
        "spacing": 4,
        "icon_size": 12,
        "show_title": False,
    },
    UIStyle.COMPACT_3: {
        "margin_v": 1,
        "margin_h": 4,
        "spacing": 2,
        "icon_size": 10,
        "show_title": False,
    },
}

# Layout constants
CHECKBOX_WIDTH = 20
LIVE_INDICATOR_WIDTH = 20
PLATFORM_LABEL_WIDTH = 20
BUTTON_PADDING = 8


class StreamRowDelegate(QStyledItemDelegate):
    """Delegate for rendering stream rows in a virtualized QListView.

    Paints all stream row elements (live indicator, platform, name, duration,
    viewers, buttons) directly without creating child widgets, making it
    orders of magnitude faster than widget-per-row approaches.
    """

    # Signals for button clicks (emitted from editorEvent)
    play_clicked = Signal(object)  # Livestream
    stop_clicked = Signal(str)  # channel_key
    favorite_clicked = Signal(str)  # channel_key
    chat_clicked = Signal(str, str, str)  # channel_id, platform, video_id
    browser_clicked = Signal(str, str)  # channel_id, platform

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        # Size cache: (unique_key, width, settings_hash) -> QSize
        self._size_cache: dict[tuple, QSize] = {}
        self._size_cache_max = 500
        # Button rects: row -> {"play": QRect, "favorite": QRect, ...}
        self._button_rects: dict[int, dict[str, QRect]] = {}
        # Font metrics cache: font_size -> (QFontMetrics, title_QFontMetrics)
        self._fm_cache: dict[int, tuple[QFontMetrics, QFontMetrics]] = {}
        # Debug counters
        self._paint_count = 0
        self._sizehint_count = 0
        self._last_debug_time = time.time()
        # Theme colors (updated via apply_theme)
        self._load_theme_colors()

    def _load_theme_colors(self) -> None:
        """Load colors from the current theme."""
        theme = get_theme()
        self._text_muted = QColor(theme.text_muted)
        self._text_primary = QColor(theme.text_primary)
        self._status_live = QColor(theme.status_live)
        self._status_error = QColor(theme.status_error)
        self._widget_bg = QColor(theme.widget_bg)
        self._selection_bg = QColor(theme.selection_bg)
        self._selection_text = QColor(theme.selection_text)
        self._accent = QColor(theme.accent)
        # Button colors
        self._input_bg = QColor(theme.input_bg)
        self._border = QColor(theme.border)

    def apply_theme(self) -> None:
        """Apply theme colors (call when theme changes).

        Note: We don't invalidate size cache here because theme colors
        don't affect row height - only layout settings do.
        """
        self._load_theme_colors()

    def invalidate_size_cache(self) -> None:
        """Clear the size cache when settings change."""
        self._size_cache.clear()
        self._fm_cache.clear()

    def _get_font_metrics(self, font_size: int) -> tuple[QFontMetrics, QFontMetrics]:
        """Get cached font metrics for the given font size.

        Returns (main_fm, title_fm) where title_fm is for the smaller title text.
        """
        if font_size in self._fm_cache:
            return self._fm_cache[font_size]

        font = QApplication.font()
        font.setPointSize(font_size)
        fm = QFontMetrics(font)

        title_font_size = max(8, int(font_size * 0.85))
        title_font = QFont(font)
        title_font.setPointSize(title_font_size)
        title_fm = QFontMetrics(title_font)

        self._fm_cache[font_size] = (fm, title_fm)
        return fm, title_fm

    def _get_settings_hash(self) -> tuple:
        """Return a tuple of settings that affect row sizing."""
        return (
            self._settings.font_size,
            self._settings.ui_style.value,
            self._settings.channel_icons.show_platform,
            self._settings.channel_icons.show_browser,
            self._settings.channel_icons.show_chat,
            self._settings.channel_icons.show_favorite,
            self._settings.channel_icons.show_play,
            self._settings.channel_info.show_live_duration,
            self._settings.channel_info.show_viewers,
        )

    def _get_style_config(self) -> dict:
        """Get the current UI style configuration."""
        return UI_STYLES.get(self._settings.ui_style, UI_STYLES[UIStyle.DEFAULT])

    def _get_font_size(self) -> int:
        """Get the current font size."""
        default_size = QApplication.font().pointSize()
        return self._settings.font_size if self._settings.font_size > 0 else default_size

    def _get_scale(self) -> float:
        """Get the font scale factor."""
        default_size = QApplication.font().pointSize()
        font_size = self._get_font_size()
        return font_size / default_size if default_size > 0 else 1.0

    def paint(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        """Paint a stream row."""
        self._paint_count += 1
        self._maybe_log_stats()

        livestream: Livestream | None = index.data(StreamRole)
        if not livestream:
            return

        is_playing = index.data(PlayingRole) or False
        is_selected_checkbox = index.data(SelectionRole) or False
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        style = self._get_style_config()
        font_size = self._get_font_size()
        scale = self._get_scale()

        # Background
        if is_selected:
            painter.fillRect(option.rect, self._selection_bg)
        else:
            painter.fillRect(option.rect, self._widget_bg)

        # Margins
        margin_h = style["margin_h"]
        margin_v = style["margin_v"]
        spacing = style["spacing"]
        icon_size = int(style["icon_size"] * scale)

        rect = option.rect.adjusted(margin_h, margin_v, -margin_h, -margin_v)
        x = rect.x()
        y = rect.y()
        row_height = rect.height()

        # Set up font
        font = painter.font()
        font.setPointSize(font_size)
        painter.setFont(font)
        fm = QFontMetrics(font)
        line_height = fm.height()

        # Store button rects for this row
        button_rects: dict[str, QRect] = {}
        row = index.row()

        # === SELECTION CHECKBOX ===
        model = index.model()
        if model and hasattr(model, "is_selection_mode") and model.is_selection_mode():
            cb_y = y + (row_height - CHECKBOX_WIDTH) // 2
            checkbox_rect = QRect(x, cb_y, CHECKBOX_WIDTH, CHECKBOX_WIDTH)
            button_rects["checkbox"] = checkbox_rect
            # Draw checkbox
            painter.setPen(self._text_muted)
            painter.drawRect(checkbox_rect.adjusted(2, 2, -2, -2))
            if is_selected_checkbox:
                pen = QPen(QColor(220, 40, 40), 2.0)
                painter.setPen(pen)
                inner = checkbox_rect.adjusted(4, 4, -4, -4)
                painter.drawLine(QLineF(inner.topLeft(), inner.bottomRight()))
                painter.drawLine(QLineF(inner.topRight(), inner.bottomLeft()))
            x += CHECKBOX_WIDTH + spacing

        # === LIVE INDICATOR (draw circle, not emoji - avoids font fallback overhead) ===
        align_lv = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        indicator_size = int(10 * scale)
        indicator_cx = x + LIVE_INDICATOR_WIDTH // 2
        indicator_cy = y + line_height // 2
        if livestream.live:
            painter.setBrush(QColor("#00FF00"))
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            painter.setBrush(self._text_muted)
            painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(
            indicator_cx - indicator_size // 2,
            indicator_cy - indicator_size // 2,
            indicator_size,
            indicator_size,
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x += LIVE_INDICATOR_WIDTH

        # === PLATFORM ICON ===
        if self._settings.channel_icons.show_platform:
            platform = livestream.channel.platform
            platform_text = PLATFORM_ICONS.get(platform, "?")
            if self._settings.platform_colors:
                color = QColor(PLATFORM_COLORS.get(platform, "#888888"))
            else:
                color = self._text_primary if is_selected else self._text_muted
            bold_font = QFont(font)
            bold_font.setBold(True)
            painter.setFont(bold_font)
            painter.setPen(color)
            painter.drawText(
                x, y, PLATFORM_LABEL_WIDTH, line_height, align_lv, platform_text
            )
            painter.setFont(font)
            x += PLATFORM_LABEL_WIDTH + spacing

        # === Calculate button widths (right side) ===
        btn_height = icon_size + BUTTON_PADDING
        btn_spacing = 4
        btn_widths = []

        if self._settings.channel_icons.show_browser:
            btn_widths.append(icon_size + BUTTON_PADDING)
        if self._settings.channel_icons.show_chat:
            btn_widths.append(icon_size + BUTTON_PADDING)
        if self._settings.channel_icons.show_favorite:
            btn_widths.append(icon_size + BUTTON_PADDING)
        if self._settings.channel_icons.show_play:
            # play button slightly wider
            btn_widths.append(icon_size + BUTTON_PADDING + 4)

        # Total width: sum of buttons + spacing between them (not after last)
        buttons_width = sum(btn_widths) + btn_spacing * max(0, len(btn_widths) - 1)

        # Available width for text content
        text_area_width = rect.right() - x - buttons_width - spacing

        # === CHANNEL NAME ===
        channel = livestream.channel
        name_text = channel.display_name or channel.channel_id
        bold_font = QFont(font)
        bold_font.setBold(True)
        painter.setFont(bold_font)

        if self._settings.platform_colors:
            name_color = QColor(PLATFORM_COLORS.get(channel.platform, "#888888"))
        else:
            name_color = self._selection_text if is_selected else self._text_primary

        painter.setPen(name_color)
        name_width = min(QFontMetrics(bold_font).horizontalAdvance(name_text), text_area_width // 2)
        painter.drawText(x, y, name_width + 10, line_height,
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name_text)
        x += name_width + 10 + spacing
        painter.setFont(font)

        # === DURATION / LAST SEEN ===
        if self._settings.channel_info.show_live_duration:
            duration_text = ""
            if livestream.live and livestream.start_time:
                duration_text = livestream.live_duration_str
            elif not livestream.live and livestream.last_live_time:
                duration_text = livestream.last_seen_str

            if duration_text:
                painter.setPen(self._selection_text if is_selected else self._text_muted)
                duration_width = fm.horizontalAdvance(duration_text) + 10
                painter.drawText(x, y, duration_width, line_height, align_lv, duration_text)
                x += duration_width + spacing

        # === PLAYING INDICATOR ===
        if is_playing:
            painter.setPen(self._status_live)
            playing_font = QFont(font)
            playing_font.setBold(True)
            painter.setFont(playing_font)
            playing_text = "\u25B6 Playing"
            playing_width = QFontMetrics(playing_font).horizontalAdvance(playing_text)
            painter.drawText(x, y, playing_width + 10, line_height,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, playing_text)
            x += playing_width + 10 + spacing
            painter.setFont(font)

        # === VIEWERS (right-aligned before buttons) ===
        btn_area_start = rect.right() - buttons_width
        viewers_gap = spacing * 2  # Gap between viewers and buttons
        if self._settings.channel_info.show_viewers and livestream.live:
            painter.setPen(self._selection_text if is_selected else self._text_muted)
            viewers_text = livestream.viewers_str
            viewers_width = fm.horizontalAdvance(viewers_text)
            # Draw viewers right-aligned, ending before button area
            viewers_x = btn_area_start - viewers_gap - viewers_width
            painter.drawText(viewers_x, y, viewers_width, line_height, align_lv, viewers_text)

        # === BUTTONS (right side) ===
        btn_x = btn_area_start
        btn_y = y + (line_height - btn_height) // 2

        # Browser button
        if self._settings.channel_icons.show_browser:
            btn_rect = QRect(btn_x, btn_y, icon_size + BUTTON_PADDING, btn_height)
            button_rects["browser"] = btn_rect
            self._draw_button(painter, btn_rect, "B", is_selected)
            btn_x += icon_size + BUTTON_PADDING + btn_spacing

        # Chat button
        if self._settings.channel_icons.show_chat:
            btn_rect = QRect(btn_x, btn_y, icon_size + BUTTON_PADDING, btn_height)
            button_rects["chat"] = btn_rect
            self._draw_button(painter, btn_rect, "C", is_selected)
            btn_x += icon_size + BUTTON_PADDING + btn_spacing

        # Favorite button
        if self._settings.channel_icons.show_favorite:
            btn_rect = QRect(btn_x, btn_y, icon_size + BUTTON_PADDING, btn_height)
            button_rects["favorite"] = btn_rect
            fav_text = "\u2605" if channel.favorite else "\u2606"  # Filled/empty star
            self._draw_button(painter, btn_rect, fav_text, is_selected)
            btn_x += icon_size + BUTTON_PADDING + btn_spacing

        # Play/Stop button
        if self._settings.channel_icons.show_play:
            btn_rect = QRect(btn_x, btn_y, icon_size + BUTTON_PADDING + 4, btn_height)
            button_rects["play"] = btn_rect
            if is_playing:
                # Stop square
                self._draw_button(painter, btn_rect, "\u25A0", is_selected, self._status_error)
            else:
                # Play triangle
                self._draw_button(painter, btn_rect, "\u25B6", is_selected)

        # === TITLE ROW (DEFAULT style only) ===
        if style["show_title"] and livestream.live:
            title_parts = []
            if livestream.game:
                title_parts.append(livestream.game)
            if livestream.title:
                title_parts.append(livestream.title)
            if title_parts:
                title_text = " - ".join(title_parts)
                title_y = y + line_height + 2
                title_font_size = max(8, int(font_size * 0.85))
                title_font = QFont(font)
                title_font.setPointSize(title_font_size)
                painter.setFont(title_font)
                painter.setPen(self._selection_text if is_selected else self._text_muted)

                # Elide title if too long
                title_width = rect.right() - rect.x() - margin_h
                title_fm = QFontMetrics(title_font)
                elided = title_fm.elidedText(title_text, Qt.TextElideMode.ElideRight, title_width)
                painter.drawText(
                    rect.x(), title_y, title_width, title_fm.height(), align_lv, elided
                )

        # Store button rects for click handling
        self._button_rects[row] = button_rects

        painter.restore()

    def _draw_button(
        self, painter: QPainter, rect: QRect, text: str,
        is_selected: bool, text_color: QColor | None = None
    ) -> None:
        """Draw a styled button with background, border, and text."""
        # Draw button background with rounded corners
        painter.setBrush(self._input_bg)
        # Use slightly darker border for better definition
        pen = QPen(self._border)
        pen.setWidth(1)
        painter.setPen(pen)
        radius = 3  # Match QPushButton default radius
        painter.drawRoundedRect(rect, radius, radius)

        # Draw button text
        if text_color:
            painter.setPen(text_color)
        else:
            painter.setPen(self._text_primary)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _maybe_log_stats(self) -> None:
        """Log paint/sizeHint stats every 5 seconds."""
        now = time.time()
        if now - self._last_debug_time >= 5.0:
            _delegate_logger.info(
                f"[DELEGATE-STATS] paint={self._paint_count}, sizeHint={self._sizehint_count} "
                f"in last 5s"
            )
            self._paint_count = 0
            self._sizehint_count = 0
            self._last_debug_time = now

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        """Calculate the size needed for a stream row."""
        self._sizehint_count += 1

        livestream: Livestream | None = index.data(StreamRole)
        if not livestream:
            return QSize(option.rect.width(), 40)

        # Calculate width
        rect_width = option.rect.width()
        if rect_width <= 0:
            parent = self.parent()
            if parent and hasattr(parent, "viewport"):
                rect_width = parent.viewport().width()
            else:
                rect_width = 600

        # Check cache
        cache_key = (livestream.channel.unique_key, rect_width, self._get_settings_hash())
        if cache_key in self._size_cache:
            return self._size_cache[cache_key]

        # Calculate height using cached font metrics
        style = self._get_style_config()
        font_size = self._get_font_size()
        fm, title_fm = self._get_font_metrics(font_size)

        # Base height: one line + margins
        height = fm.height() + style["margin_v"] * 2

        # Add title row height for DEFAULT style when live
        if style["show_title"] and livestream.live and (livestream.game or livestream.title):
            height += title_fm.height() + 4

        result = QSize(rect_width, height)

        # Store in cache with eviction
        if len(self._size_cache) >= self._size_cache_max:
            keys = list(self._size_cache.keys())
            for k in keys[: len(keys) // 2]:
                del self._size_cache[k]
        self._size_cache[cache_key] = result

        return result

    def editorEvent(  # noqa: N802
        self, event: QEvent, model, option: QStyleOptionViewItem, index: QModelIndex
    ) -> bool:
        """Handle mouse events for button clicks."""
        if event.type() != QEvent.Type.MouseButtonRelease:
            return False

        pos = event.pos()
        row = index.row()
        rects = self._button_rects.get(row, {})

        livestream: Livestream | None = index.data(StreamRole)
        if not livestream:
            return False

        is_playing = index.data(PlayingRole) or False
        channel = livestream.channel

        # Check checkbox click
        if "checkbox" in rects and rects["checkbox"].contains(pos):
            if model and hasattr(model, "toggle_selection"):
                model.toggle_selection(index)
            return True

        # Check play/stop button
        if "play" in rects and rects["play"].contains(pos):
            if is_playing:
                self.stop_clicked.emit(channel.unique_key)
            else:
                self.play_clicked.emit(livestream)
            return True

        # Check favorite button
        if "favorite" in rects and rects["favorite"].contains(pos):
            self.favorite_clicked.emit(channel.unique_key)
            return True

        # Check chat button
        if "chat" in rects and rects["chat"].contains(pos):
            video_id = getattr(livestream, "video_id", None) or ""
            self.chat_clicked.emit(channel.channel_id, channel.platform.value, video_id)
            return True

        # Check browser button
        if "browser" in rects and rects["browser"].contains(pos):
            self.browser_clicked.emit(channel.channel_id, channel.platform.value)
            return True

        return False

    def clear_button_rects(self) -> None:
        """Clear cached button rects (call on scroll or model reset)."""
        self._button_rects.clear()
