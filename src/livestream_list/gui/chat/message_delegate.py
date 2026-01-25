"""Custom delegate for painting chat messages."""

import re

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QHelpEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QWidget,
)

from ...chat.models import ChatMessage
from ...core.settings import BuiltinChatSettings
from .message_model import MessageRole

# Layout constants
PADDING_H = 8
BADGE_SPACING = 2
USERNAME_SPACING = 4
TIMESTAMP_PADDING = 6  # Small gap after timestamp text

# Mod-related badge names
MOD_BADGE_NAMES = {"moderator", "vip", "staff", "admin", "broadcaster"}

# URL detection
URL_RE = re.compile(r'https?://[^\s<>\[\]"\'`)\]]+')
URL_COLOR = QColor("#58a6ff")

# Shared alignment flags
ALIGN_LEFT_VCENTER = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
ALIGN_WRAP = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap


class ChatMessageDelegate(QStyledItemDelegate):
    """Delegate for rendering chat messages in a QListView.

    Paints badges, colored usernames, message text with inline emotes,
    and handles moderation display (strikethrough + opacity).
    Emotes and badges scale to match the configured font size.
    """

    def __init__(self, settings: BuiltinChatSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self._emote_cache: dict[str, QPixmap] = {}
        self._animated_cache: dict[str, list[QPixmap]] = {}
        self._animation_frame: int = 0
        # Cache for sizeHint calculations: (msg_id, width, settings_hash) -> QSize
        self._size_cache: dict[tuple, QSize] = {}
        self._size_cache_max = 500

    def set_emote_cache(self, cache: dict[str, QPixmap]) -> None:
        """Set the shared emote pixmap cache."""
        self._emote_cache = cache

    def set_animated_cache(self, cache: dict[str, list[QPixmap]]) -> None:
        """Set the shared animated frame cache."""
        self._animated_cache = cache

    def set_animation_frame(self, frame: int) -> None:
        """Set the current global animation frame counter."""
        self._animation_frame = frame

    def invalidate_size_cache(self) -> None:
        """Clear the sizeHint cache when settings change or on resize."""
        self._size_cache.clear()

    def _get_settings_hash(self) -> tuple:
        """Return a tuple of settings that affect message sizing."""
        return (
            self.settings.font_size,
            self.settings.line_spacing,
            self.settings.show_timestamps,
            self.settings.show_badges,
            self.settings.show_mod_badges,
            self.settings.show_emotes,
        )

    def _get_emote_height(self, fm: QFontMetrics) -> int:
        """Get emote height scaled to font size."""
        return int(fm.height() * 1.3)

    def _get_badge_size(self, fm: QFontMetrics) -> int:
        """Get badge size scaled to font size."""
        return max(fm.height(), 10)

    def paint(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        """Paint a chat message."""
        message: ChatMessage | None = index.data(MessageRole)
        if not message:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if is_selected:
            painter.fillRect(option.rect, option.palette.highlight())
        elif message.is_system:
            painter.fillRect(option.rect, QColor(100, 50, 150, 40))
        elif message.is_hype_chat:
            painter.fillRect(option.rect, QColor(200, 170, 50, 30))
            # Left accent bar for hype chat
            accent_rect = QRect(option.rect.x(), option.rect.y(), 3, option.rect.height())
            painter.fillRect(accent_rect, QColor(218, 165, 32))
        elif message.is_mention:
            painter.fillRect(option.rect, QColor(self.settings.mention_highlight_color))
        elif self.settings.show_alternating_rows:
            if index.row() % 2 == 0:
                color = QColor(self.settings.alt_row_color_even)
            else:
                color = QColor(self.settings.alt_row_color_odd)
            if color.alpha() > 0:
                painter.fillRect(option.rect, color)

        # Apply moderation opacity
        if message.is_moderated:
            painter.setOpacity(0.5)

        padding_v = self.settings.line_spacing
        rect = option.rect.adjusted(PADDING_H, padding_v, -PADDING_H, -padding_v)
        x = rect.x()
        y = rect.y()
        available_width = rect.width()

        font = painter.font()
        font.setPointSize(self.settings.font_size)
        painter.setFont(font)
        fm = QFontMetrics(font)

        badge_size = self._get_badge_size(fm)
        emote_height = self._get_emote_height(fm)
        line_height = max(fm.height(), badge_size)

        # Highlight text color for selected state
        highlight_color = option.palette.highlightedText().color() if is_selected else None

        # Timestamp (optional)
        if self.settings.show_timestamps:
            ts_text = message.timestamp.strftime("%H:%M")
            painter.setPen(highlight_color if is_selected else QColor(128, 128, 128))
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            painter.setFont(ts_font)
            ts_width = QFontMetrics(ts_font).horizontalAdvance(ts_text) + TIMESTAMP_PADDING
            painter.drawText(x, y, ts_width, line_height, ALIGN_LEFT_VCENTER, ts_text)
            x += ts_width
            painter.setFont(font)

        # System message text (USERNOTICE - subs, raids, etc.)
        if message.is_system and message.system_text:
            italic_font = QFont(font)
            italic_font.setItalic(True)
            painter.setFont(italic_font)
            painter.setPen(highlight_color if is_selected else QColor(190, 150, 255))
            sys_text = message.system_text
            sys_width = QFontMetrics(italic_font).horizontalAdvance(sys_text)
            remaining = available_width - (x - rect.x())
            painter.drawText(x, y, remaining, line_height * 2, ALIGN_WRAP, sys_text)
            painter.setFont(font)
            # If there's user text, move to next line
            if message.text:
                sys_lines = max(1, int(sys_width / remaining) + 1) if remaining > 0 else 1
                y += line_height * sys_lines
                x = rect.x()
            else:
                painter.restore()
                return

        # Badges
        if message.user.badges and (self.settings.show_badges or self.settings.show_mod_badges):
            badge_y = y + (line_height - badge_size) // 2
            for badge in message.user.badges:
                is_mod_badge = badge.name in MOD_BADGE_NAMES
                # Skip non-mod badges if show_badges is off
                if not self.settings.show_badges and not is_mod_badge:
                    continue
                # Skip mod badges if show_mod_badges is off
                if not self.settings.show_mod_badges and is_mod_badge:
                    continue
                badge_key = f"badge:{badge.id}"
                pixmap = self._emote_cache.get(badge_key)
                if pixmap and not pixmap.isNull():
                    painter.drawPixmap(x, badge_y, badge_size, badge_size, pixmap)
                    x += badge_size + BADGE_SPACING

        # Username
        bold_font = QFont(font)
        bold_font.setBold(True)
        painter.setFont(bold_font)

        if self.settings.use_platform_name_colors:
            user_color = QColor(message.user.color) if message.user.color else QColor(180, 130, 255)
        else:
            user_color = QColor(100, 180, 220)
        painter.setPen(highlight_color if is_selected else user_color)

        name_text = message.user.display_name
        if message.is_action:
            name_width = QFontMetrics(bold_font).horizontalAdvance(name_text + " ")
            painter.drawText(x, y, name_width, line_height, ALIGN_LEFT_VCENTER, name_text)
            x += name_width
        else:
            name_with_colon = name_text + ": "
            name_width = QFontMetrics(bold_font).horizontalAdvance(name_with_colon)
            painter.drawText(x, y, name_width, line_height, ALIGN_LEFT_VCENTER, name_with_colon)
            x += name_width

        # Message text (with inline emotes)
        painter.setFont(font)
        if is_selected:
            painter.setPen(highlight_color)
        elif message.is_action:
            painter.setPen(user_color)
        else:
            painter.setPen(option.palette.text().color())

        msg_text = message.text
        text_x = x
        text_y = y

        if self.settings.show_emotes and message.emote_positions:
            self._paint_text_with_emotes(
                painter,
                msg_text,
                message.emote_positions,
                text_x,
                text_y,
                line_height,
                emote_height,
                available_width - (text_x - rect.x()),
                fm,
                message.is_moderated,
            )
        else:
            remaining_width = available_width - (text_x - rect.x())
            self._paint_wrapped_text(
                painter,
                msg_text,
                text_x,
                text_y,
                remaining_width,
                line_height,
                fm,
                message.is_moderated,
            )

        painter.restore()

    def _paint_text_with_emotes(
        self,
        painter: QPainter,
        text: str,
        emote_positions: list,
        x: int,
        y: int,
        line_height: int,
        emote_height: int,
        available_width: int,
        fm: QFontMetrics,
        is_moderated: bool,
    ) -> None:
        """Paint text with inline emotes, wrapping at available_width."""
        start_x = x
        current_x = x
        current_y = y
        right_edge = x + available_width
        last_end = 0
        url_ranges = self._get_url_ranges(text)

        for start, end, emote in emote_positions:
            # Draw text before emote (word-by-word wrapping)
            if start > last_end:
                segment = text[last_end:start]
                current_x, current_y = self._draw_wrapping_text(
                    painter,
                    segment,
                    current_x,
                    current_y,
                    start_x,
                    right_edge,
                    line_height,
                    fm,
                    is_moderated,
                    url_ranges=url_ranges,
                    text_offset=last_end,
                )

            # Draw emote (wrap whole emote if it doesn't fit)
            emote_key = f"emote:{emote.provider}:{emote.id}"
            frames = self._animated_cache.get(emote_key)
            if frames and self.settings.animate_emotes:
                frame_idx = self._animation_frame % len(frames)
                pixmap = frames[frame_idx]
            elif frames:
                pixmap = frames[0]
            else:
                pixmap = self._emote_cache.get(emote_key)
            if pixmap and not pixmap.isNull():
                smooth = Qt.TransformationMode.SmoothTransformation
                scaled = pixmap.scaledToHeight(emote_height, smooth)
                emote_w = scaled.width()
                if current_x + emote_w > right_edge and current_x > start_x:
                    current_x = start_x
                    current_y += line_height
                emote_y = current_y + (line_height - emote_height) // 2
                painter.drawPixmap(int(current_x), int(emote_y), scaled)
                current_x += emote_w
            else:
                emote_text = text[start:end] if end <= len(text) else emote.name
                current_x, current_y = self._draw_wrapping_text(
                    painter,
                    emote_text,
                    current_x,
                    current_y,
                    start_x,
                    right_edge,
                    line_height,
                    fm,
                    is_moderated,
                )

            last_end = end

        # Draw remaining text after last emote
        if last_end < len(text):
            segment = text[last_end:]
            self._draw_wrapping_text(
                painter,
                segment,
                current_x,
                current_y,
                start_x,
                right_edge,
                line_height,
                fm,
                is_moderated,
                url_ranges=url_ranges,
                text_offset=last_end,
            )

    @staticmethod
    def _get_url_ranges(text: str) -> list[tuple[int, int, str]]:
        """Find all URLs in text, returning (start, end, url) tuples."""
        return [(m.start(), m.end(), m.group()) for m in URL_RE.finditer(text)]

    def _draw_wrapping_text(
        self,
        painter: QPainter,
        text: str,
        current_x: int,
        current_y: int,
        start_x: int,
        right_edge: int,
        line_height: int,
        fm: QFontMetrics,
        is_moderated: bool,
        url_ranges: list[tuple[int, int, str]] | None = None,
        text_offset: int = 0,
    ) -> tuple[int, int]:
        """Draw text word-by-word, wrapping to the next line as needed.

        Returns (current_x, current_y) after drawing.
        url_ranges are absolute (start, end, url) tuples in the full message text.
        text_offset is the position of this segment in the full message text.
        """
        words = text.split(" ")
        char_pos = 0
        for i, word in enumerate(words):
            # Add space before word (except first word if at line start after emote)
            draw_word = word if i == 0 else " " + word
            word_width = fm.horizontalAdvance(draw_word)

            # Compute absolute position of the word (excluding leading space)
            if i == 0:
                abs_word_start = text_offset + char_pos
            else:
                abs_word_start = text_offset + char_pos + 1  # skip space
            abs_word_end = abs_word_start + len(word)

            # Wrap if this word doesn't fit (but not if we're at the start of a line)
            if current_x + word_width > right_edge and current_x > start_x:
                current_x = start_x
                current_y += line_height
                # Remove leading space after wrap
                if draw_word.startswith(" "):
                    draw_word = draw_word[1:]
                    word_width = fm.horizontalAdvance(draw_word)

            # Check if word is part of a URL
            in_url = False
            if url_ranges and not is_moderated:
                for url_start, url_end, _ in url_ranges:
                    if abs_word_start < url_end and abs_word_end > url_start:
                        in_url = True
                        break

            if draw_word:
                if in_url:
                    saved_pen = painter.pen()
                    painter.setPen(URL_COLOR)
                    font = painter.font()
                    font.setUnderline(True)
                    painter.setFont(font)
                    painter.drawText(
                        int(current_x),
                        int(current_y),
                        word_width + 2,
                        line_height,
                        ALIGN_LEFT_VCENTER,
                        draw_word,
                    )
                    font.setUnderline(False)
                    painter.setFont(font)
                    painter.setPen(saved_pen)
                elif is_moderated:
                    self._draw_strikethrough_text(
                        painter,
                        current_x,
                        current_y,
                        word_width,
                        line_height,
                        draw_word,
                        fm,
                    )
                else:
                    painter.drawText(
                        int(current_x),
                        int(current_y),
                        word_width + 2,
                        line_height,
                        ALIGN_LEFT_VCENTER,
                        draw_word,
                    )
                current_x += word_width

            # Advance char_pos
            char_pos += len(draw_word) if i == 0 else 1 + len(word)

        return current_x, current_y

    def _paint_wrapped_text(
        self,
        painter: QPainter,
        text: str,
        x: int,
        y: int,
        available_width: int,
        line_height: int,
        fm: QFontMetrics,
        is_moderated: bool,
    ) -> None:
        """Paint text with word wrapping."""
        right_edge = x + available_width
        url_ranges = self._get_url_ranges(text)
        self._draw_wrapping_text(
            painter,
            text,
            x,
            y,
            x,
            right_edge,
            line_height,
            fm,
            is_moderated,
            url_ranges=url_ranges,
            text_offset=0,
        )

    def _draw_strikethrough_text(
        self,
        painter: QPainter,
        x: int,
        y: int,
        width: int,
        height: int,
        text: str,
        fm: QFontMetrics,
    ) -> None:
        """Draw text with strikethrough for moderated messages."""
        font = painter.font()
        font.setStrikeOut(True)
        painter.setFont(font)
        painter.drawText(x, y, width, height, ALIGN_LEFT_VCENTER, text)
        font.setStrikeOut(False)
        painter.setFont(font)

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        """Calculate the size needed for a message.

        Uses caching to avoid expensive recalculation on every layout pass.
        Cache is keyed by message ID, available width, and relevant settings.
        """
        message: ChatMessage | None = index.data(MessageRole)
        if not message:
            return QSize(option.rect.width(), 24)

        # Calculate available width early for cache lookup
        rect_width = option.rect.width()
        if rect_width <= 0:
            parent = self.parent()
            if parent and hasattr(parent, "viewport"):
                rect_width = parent.viewport().width()
            else:
                rect_width = 400
        available_width = max(rect_width - PADDING_H * 2, 200)

        # Check cache
        cache_key = (message.id, available_width, self._get_settings_hash())
        if cache_key in self._size_cache:
            return self._size_cache[cache_key]

        # Cache miss - calculate size
        padding_v = self.settings.line_spacing
        font = option.font
        font.setPointSize(self.settings.font_size)
        fm = QFontMetrics(font)

        badge_size = self._get_badge_size(fm)
        emote_height = self._get_emote_height(fm)
        line_height = max(fm.height(), badge_size) + padding_v

        # Prefix width (timestamp + badges + username)
        prefix_width = 0
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            prefix_width += QFontMetrics(ts_font).horizontalAdvance("00:00") + TIMESTAMP_PADDING
        if message.user.badges and (self.settings.show_badges or self.settings.show_mod_badges):
            badge_count = 0
            for b in message.user.badges:
                is_mod = b.name in MOD_BADGE_NAMES
                if not self.settings.show_badges and not is_mod:
                    continue
                if not self.settings.show_mod_badges and is_mod:
                    continue
                badge_count += 1
            prefix_width += badge_count * (badge_size + BADGE_SPACING)

        bold_font = QFont(font)
        bold_font.setBold(True)
        suffix = ": " if not message.is_action else " "
        name_text = message.user.display_name + suffix
        prefix_width += QFontMetrics(bold_font).horizontalAdvance(name_text)

        # Calculate number of lines needed via wrapping simulation
        content_width = available_width - prefix_width
        if content_width <= 0:
            content_width = available_width

        if self.settings.show_emotes and message.emote_positions:
            lines = self._compute_wrapped_lines_with_emotes(
                message.text, message.emote_positions, content_width, fm, emote_height
            )
        else:
            text_width = fm.horizontalAdvance(message.text)
            lines = 1
            if text_width > content_width:
                # Simulate word wrapping
                lines = self._compute_wrapped_lines(message.text, content_width, fm)

        # System messages need extra lines for system_text
        extra_lines = 0
        if message.is_system and message.system_text:
            sys_font = QFont(font)
            sys_font.setItalic(True)
            sys_width = QFontMetrics(sys_font).horizontalAdvance(message.system_text)
            sys_available = max(available_width - prefix_width, 200)
            extra_lines = max(1, int(sys_width / sys_available) + 1) if sys_available > 0 else 1
            if not message.text:
                lines = 0  # Only system text, no user message line

        height = max(
            line_height * (lines + extra_lines) + padding_v * 2,
            emote_height + padding_v * 2,
        )
        result = QSize(available_width, height)

        # Store in cache (with size limit to prevent unbounded growth)
        if len(self._size_cache) >= self._size_cache_max:
            # Simple eviction: clear half the cache when full
            keys = list(self._size_cache.keys())
            for k in keys[: len(keys) // 2]:
                del self._size_cache[k]
        self._size_cache[cache_key] = result

        return result

    def helpEvent(  # noqa: N802
        self,
        event: QHelpEvent,
        view: QAbstractItemView,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        """Show emote tooltip on hover."""
        if not isinstance(event, QHelpEvent):
            return super().helpEvent(event, view, option, index)

        message: ChatMessage | None = index.data(MessageRole)
        if not message or not message.emote_positions:
            QToolTip.hideText()
            return True

        emote = self._get_emote_at_position(event.pos(), option, message)
        if emote:
            provider_names = {
                "twitch": "Twitch",
                "kick": "Kick",
                "7tv": "7TV",
                "bttv": "BTTV",
                "ffz": "FFZ",
            }
            provider = provider_names.get(emote.provider, emote.provider)
            QToolTip.showText(event.globalPos(), f"{emote.name}\n({provider})")
        else:
            QToolTip.hideText()
        return True

    def _get_username_rect(self, option: QStyleOptionViewItem, message: ChatMessage) -> QRect:
        """Get the bounding rect of the username in a message item."""
        padding_v = self.settings.line_spacing
        rect = option.rect.adjusted(PADDING_H, padding_v, -PADDING_H, -padding_v)
        x = rect.x()
        y = rect.y()

        font = option.font
        font.setPointSize(self.settings.font_size)
        fm = QFontMetrics(font)

        badge_size = self._get_badge_size(fm)
        line_height = max(fm.height(), badge_size)

        # Skip timestamp
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            x += QFontMetrics(ts_font).horizontalAdvance("00:00") + TIMESTAMP_PADDING

        # Skip system text lines
        if message.is_system and message.system_text:
            if message.text:
                sys_font = QFont(font)
                sys_font.setItalic(True)
                sys_width = QFontMetrics(sys_font).horizontalAdvance(message.system_text)
                remaining = rect.width() - (x - rect.x())
                sys_lines = max(1, int(sys_width / remaining) + 1) if remaining > 0 else 1
                y += line_height * sys_lines
                x = rect.x()
            else:
                return QRect()

        # Skip badges
        if self.settings.show_badges and message.user.badges:
            for badge in message.user.badges:
                if not self.settings.show_mod_badges and badge.name in MOD_BADGE_NAMES:
                    continue
                x += badge_size + BADGE_SPACING

        # Username rect
        bold_font = QFont(font)
        bold_font.setBold(True)
        name_text = message.user.display_name
        name_width = QFontMetrics(bold_font).horizontalAdvance(name_text)
        return QRect(int(x), int(y), int(name_width), line_height)

    def _get_emote_at_position(self, pos, option: QStyleOptionViewItem, message: ChatMessage):
        """Find which emote (if any) is at the given position, accounting for wrapping."""
        padding_v = self.settings.line_spacing
        rect = option.rect.adjusted(PADDING_H, padding_v, -PADDING_H, -padding_v)
        x = rect.x()
        y = rect.y()

        font = option.font
        font.setPointSize(self.settings.font_size)
        fm = QFontMetrics(font)

        badge_size = self._get_badge_size(fm)
        emote_height = self._get_emote_height(fm)
        line_height = max(fm.height(), badge_size)

        # Skip timestamp
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            x += QFontMetrics(ts_font).horizontalAdvance("00:00") + TIMESTAMP_PADDING

        # Skip system text lines
        if message.is_system and message.system_text:
            if message.text:
                sys_font = QFont(font)
                sys_font.setItalic(True)
                sys_width = QFontMetrics(sys_font).horizontalAdvance(message.system_text)
                remaining = rect.width() - (x - rect.x())
                sys_lines = max(1, int(sys_width / remaining) + 1) if remaining > 0 else 1
                y += line_height * sys_lines
                x = rect.x()
            else:
                return None

        # Skip badges
        if self.settings.show_badges and message.user.badges:
            for badge in message.user.badges:
                if not self.settings.show_mod_badges and badge.name in MOD_BADGE_NAMES:
                    continue
                x += badge_size + BADGE_SPACING

        # Skip username
        bold_font = QFont(font)
        bold_font.setBold(True)
        suffix = " " if message.is_action else ": "
        name_text = message.user.display_name + suffix
        x += QFontMetrics(bold_font).horizontalAdvance(name_text)

        # Walk through text + emotes with wrapping logic
        text = message.text
        last_end = 0
        start_x = rect.x()
        current_x = x
        current_y = y
        available_width = rect.width()
        right_edge = rect.x() + available_width

        for start, end, emote in message.emote_positions:
            # Advance past text before emote (with wrapping)
            if start > last_end:
                segment = text[last_end:start]
                current_x, current_y = self._advance_wrapping_text(
                    segment,
                    current_x,
                    current_y,
                    start_x,
                    right_edge,
                    line_height,
                    fm,
                )

            # Emote rect (with wrapping)
            emote_key = f"emote:{emote.provider}:{emote.id}"
            pixmap = self._emote_cache.get(emote_key)
            if pixmap and not pixmap.isNull():
                smooth = Qt.TransformationMode.SmoothTransformation
                scaled = pixmap.scaledToHeight(emote_height, smooth)
                emote_w = scaled.width()
            else:
                emote_text = text[start:end] if end <= len(text) else emote.name
                emote_w = fm.horizontalAdvance(emote_text)

            # Wrap emote if needed
            if current_x + emote_w > right_edge and current_x > start_x:
                current_x = start_x
                current_y += line_height

            emote_y_pos = current_y + (line_height - emote_height) // 2
            emote_rect = QRect(int(current_x), int(emote_y_pos), int(emote_w), emote_height)
            if emote_rect.contains(pos):
                return emote
            current_x += emote_w
            last_end = end

        return None

    def _get_url_at_position(
        self, pos, option: QStyleOptionViewItem, message: ChatMessage
    ) -> str | None:
        """Find which URL (if any) is at the given position, accounting for wrapping."""
        url_ranges = self._get_url_ranges(message.text)
        if not url_ranges:
            return None

        padding_v = self.settings.line_spacing
        rect = option.rect.adjusted(PADDING_H, padding_v, -PADDING_H, -padding_v)
        x = rect.x()
        y = rect.y()

        font = option.font
        font.setPointSize(self.settings.font_size)
        fm = QFontMetrics(font)

        badge_size = self._get_badge_size(fm)
        line_height = max(fm.height(), badge_size)

        # Skip timestamp
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            x += QFontMetrics(ts_font).horizontalAdvance("00:00") + TIMESTAMP_PADDING

        # Skip system text lines
        if message.is_system and message.system_text:
            if message.text:
                sys_font = QFont(font)
                sys_font.setItalic(True)
                sys_width = QFontMetrics(sys_font).horizontalAdvance(message.system_text)
                remaining = rect.width() - (x - rect.x())
                sys_lines = max(1, int(sys_width / remaining) + 1) if remaining > 0 else 1
                y += line_height * sys_lines
                x = rect.x()
            else:
                return None

        # Skip badges
        if message.user.badges and (self.settings.show_badges or self.settings.show_mod_badges):
            for badge in message.user.badges:
                is_mod = badge.name in MOD_BADGE_NAMES
                if not self.settings.show_badges and not is_mod:
                    continue
                if not self.settings.show_mod_badges and is_mod:
                    continue
                x += badge_size + BADGE_SPACING

        # Skip username
        bold_font = QFont(font)
        bold_font.setBold(True)
        suffix = " " if message.is_action else ": "
        name_text = message.user.display_name + suffix
        x += QFontMetrics(bold_font).horizontalAdvance(name_text)

        # Walk through text with wrapping, checking URL word positions
        text = message.text
        start_x = rect.x()
        current_x = x
        current_y = y
        right_edge = rect.x() + rect.width()

        if message.emote_positions:
            # Walk through text segments between emotes
            last_end = 0
            for em_start, em_end, emote in message.emote_positions:
                if em_start > last_end:
                    segment = text[last_end:em_start]
                    result = self._check_url_words_at_pos(
                        segment,
                        current_x,
                        current_y,
                        start_x,
                        right_edge,
                        line_height,
                        fm,
                        pos,
                        url_ranges,
                        last_end,
                    )
                    if result:
                        return result
                    current_x, current_y = self._advance_wrapping_text(
                        segment,
                        current_x,
                        current_y,
                        start_x,
                        right_edge,
                        line_height,
                        fm,
                    )
                # Skip emote
                emote_height = self._get_emote_height(fm)
                emote_key = f"emote:{emote.provider}:{emote.id}"
                pixmap = self._emote_cache.get(emote_key)
                if pixmap and not pixmap.isNull():
                    scaled = pixmap.scaledToHeight(
                        emote_height, Qt.TransformationMode.SmoothTransformation
                    )
                    emote_w = scaled.width()
                else:
                    emote_text = text[em_start:em_end] if em_end <= len(text) else emote.name
                    emote_w = fm.horizontalAdvance(emote_text)
                if current_x + emote_w > right_edge and current_x > start_x:
                    current_x = start_x
                    current_y += line_height
                current_x += emote_w
                last_end = em_end

            # Remaining text after last emote
            if last_end < len(text):
                segment = text[last_end:]
                result = self._check_url_words_at_pos(
                    segment,
                    current_x,
                    current_y,
                    start_x,
                    right_edge,
                    line_height,
                    fm,
                    pos,
                    url_ranges,
                    last_end,
                )
                if result:
                    return result
        else:
            result = self._check_url_words_at_pos(
                text,
                current_x,
                current_y,
                start_x,
                right_edge,
                line_height,
                fm,
                pos,
                url_ranges,
                0,
            )
            if result:
                return result

        return None

    def _check_url_words_at_pos(
        self,
        text: str,
        current_x: int,
        current_y: int,
        start_x: int,
        right_edge: int,
        line_height: int,
        fm: QFontMetrics,
        pos,
        url_ranges: list[tuple[int, int, str]],
        text_offset: int,
    ) -> str | None:
        """Check if pos hits any URL word in the given text segment."""
        words = text.split(" ")
        char_pos = 0
        for i, word in enumerate(words):
            draw_word = word if i == 0 else " " + word
            word_width = fm.horizontalAdvance(draw_word)

            if i == 0:
                abs_word_start = text_offset + char_pos
            else:
                abs_word_start = text_offset + char_pos + 1
            abs_word_end = abs_word_start + len(word)

            if current_x + word_width > right_edge and current_x > start_x:
                current_x = start_x
                current_y += line_height
                if draw_word.startswith(" "):
                    draw_word = draw_word[1:]
                    word_width = fm.horizontalAdvance(draw_word)

            # Check if this word is in a URL and if pos hits it
            for url_start, url_end, url in url_ranges:
                if abs_word_start < url_end and abs_word_end > url_start:
                    word_rect = QRect(
                        int(current_x),
                        int(current_y),
                        int(word_width),
                        line_height,
                    )
                    if word_rect.contains(pos):
                        return url
                    break

            current_x += word_width
            char_pos += len(draw_word) if i == 0 else 1 + len(word)

        return None

    def _advance_wrapping_text(
        self,
        text: str,
        current_x: int,
        current_y: int,
        start_x: int,
        right_edge: int,
        line_height: int,
        fm: QFontMetrics,
    ) -> tuple[int, int]:
        """Advance position through text with word wrapping (no drawing).

        Returns (current_x, current_y) after the text.
        """
        words = text.split(" ")
        for i, word in enumerate(words):
            draw_word = word if i == 0 else " " + word
            word_width = fm.horizontalAdvance(draw_word)
            if current_x + word_width > right_edge and current_x > start_x:
                current_x = start_x
                current_y += line_height
                if draw_word.startswith(" "):
                    draw_word = draw_word[1:]
                    word_width = fm.horizontalAdvance(draw_word)
            current_x += word_width
        return current_x, current_y

    def _get_badge_at_position(self, pos, option: QStyleOptionViewItem, message: ChatMessage):
        """Find which badge (if any) is at the given position."""
        if not self.settings.show_badges or not message.user.badges:
            return None

        padding_v = self.settings.line_spacing
        rect = option.rect.adjusted(PADDING_H, padding_v, -PADDING_H, -padding_v)
        x = rect.x()
        y = rect.y()

        font = option.font
        font.setPointSize(self.settings.font_size)
        fm = QFontMetrics(font)

        badge_size = self._get_badge_size(fm)
        line_height = max(fm.height(), badge_size)

        # Skip timestamp
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            x += QFontMetrics(ts_font).horizontalAdvance("00:00") + TIMESTAMP_PADDING

        # Skip system text lines
        if message.is_system and message.system_text:
            if message.text:
                sys_font = QFont(font)
                sys_font.setItalic(True)
                sys_width = QFontMetrics(sys_font).horizontalAdvance(message.system_text)
                remaining = rect.width() - (x - rect.x())
                sys_lines = max(1, int(sys_width / remaining) + 1) if remaining > 0 else 1
                y += line_height * sys_lines
                x = rect.x()
            else:
                return None

        # Check each badge
        badge_y = y + (line_height - badge_size) // 2
        for badge in message.user.badges:
            if not self.settings.show_mod_badges and badge.name in MOD_BADGE_NAMES:
                continue
            badge_rect = QRect(int(x), int(badge_y), badge_size, badge_size)
            if badge_rect.contains(pos):
                return badge
            x += badge_size + BADGE_SPACING

        return None

    def _compute_wrapped_lines(
        self,
        text: str,
        content_width: int,
        fm: QFontMetrics,
    ) -> int:
        """Compute how many lines text needs with word wrapping."""
        lines = 1
        current_x = 0
        words = text.split(" ")
        for i, word in enumerate(words):
            draw_word = word if i == 0 else " " + word
            word_width = fm.horizontalAdvance(draw_word)
            if current_x + word_width > content_width and current_x > 0:
                lines += 1
                current_x = 0
                if draw_word.startswith(" "):
                    draw_word = draw_word[1:]
                    word_width = fm.horizontalAdvance(draw_word)
            current_x += word_width
        return lines

    def _compute_wrapped_lines_with_emotes(
        self,
        text: str,
        emote_positions: list,
        content_width: int,
        fm: QFontMetrics,
        emote_height: int,
    ) -> int:
        """Compute how many lines text+emotes need with word wrapping."""
        lines = 1
        current_x = 0
        last_end = 0

        for start, end, emote in emote_positions:
            # Text before emote
            if start > last_end:
                segment = text[last_end:start]
                current_x, lines = self._advance_line_count(
                    segment, current_x, lines, content_width, fm
                )

            # Emote width
            emote_key = f"emote:{emote.provider}:{emote.id}"
            pixmap = self._emote_cache.get(emote_key)
            if pixmap and not pixmap.isNull():
                smooth = Qt.TransformationMode.SmoothTransformation
                scaled = pixmap.scaledToHeight(emote_height, smooth)
                emote_w = scaled.width()
            else:
                emote_text = text[start:end] if end <= len(text) else emote.name
                emote_w = fm.horizontalAdvance(emote_text)

            if current_x + emote_w > content_width and current_x > 0:
                lines += 1
                current_x = 0
            current_x += emote_w
            last_end = end

        # Remaining text
        if last_end < len(text):
            segment = text[last_end:]
            _, lines = self._advance_line_count(segment, current_x, lines, content_width, fm)

        return lines

    def _advance_line_count(
        self,
        text: str,
        current_x: int,
        lines: int,
        content_width: int,
        fm: QFontMetrics,
    ) -> tuple[int, int]:
        """Advance line count through text with word wrapping (no drawing).

        Returns (current_x, lines).
        """
        words = text.split(" ")
        for i, word in enumerate(words):
            draw_word = word if i == 0 else " " + word
            word_width = fm.horizontalAdvance(draw_word)
            if current_x + word_width > content_width and current_x > 0:
                lines += 1
                current_x = 0
                if draw_word.startswith(" "):
                    draw_word = draw_word[1:]
                    word_width = fm.horizontalAdvance(draw_word)
            current_x += word_width
        return current_x, lines
