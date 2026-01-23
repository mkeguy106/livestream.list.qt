"""Custom delegate for painting chat messages."""

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

# Shared alignment flags
ALIGN_LEFT_VCENTER = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
ALIGN_WRAP = (
    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
)


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

    def set_emote_cache(self, cache: dict[str, QPixmap]) -> None:
        """Set the shared emote pixmap cache."""
        self._emote_cache = cache

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
        elif self.settings.show_alternating_rows and index.row() % 2 == 1:
            painter.fillRect(option.rect, QColor(255, 255, 255, 15))

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
            painter.drawText(
                x, y, ts_width, line_height, ALIGN_LEFT_VCENTER, ts_text
            )
            x += ts_width
            available_width -= ts_width
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
            painter.drawText(
                x, y, remaining, line_height * 2, ALIGN_WRAP, sys_text
            )
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
        if self.settings.show_badges and message.user.badges:
            badge_y = y + (line_height - badge_size) // 2
            for badge in message.user.badges:
                # Filter mod badges if disabled
                if (
                    not self.settings.show_mod_badges
                    and badge.name in MOD_BADGE_NAMES
                ):
                    continue
                badge_key = f"badge:{badge.id}"
                pixmap = self._emote_cache.get(badge_key)
                if pixmap and not pixmap.isNull():
                    painter.drawPixmap(
                        x, badge_y, badge_size, badge_size, pixmap
                    )
                    x += badge_size + BADGE_SPACING
                    available_width -= badge_size + BADGE_SPACING

        # Username
        bold_font = QFont(font)
        bold_font.setBold(True)
        painter.setFont(bold_font)

        if self.settings.use_platform_name_colors:
            user_color = (
                QColor(message.user.color)
                if message.user.color
                else QColor(180, 130, 255)
            )
        else:
            user_color = option.palette.text().color()
        painter.setPen(highlight_color if is_selected else user_color)

        name_text = message.user.display_name
        if message.is_action:
            name_width = QFontMetrics(bold_font).horizontalAdvance(
                name_text + " "
            )
            painter.drawText(
                x, y, name_width, line_height, ALIGN_LEFT_VCENTER, name_text
            )
            x += name_width
        else:
            name_with_colon = name_text + ": "
            name_width = QFontMetrics(bold_font).horizontalAdvance(name_with_colon)
            painter.drawText(
                x, y, name_width, line_height, ALIGN_LEFT_VCENTER, name_with_colon
            )
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
        """Paint text with inline emotes."""
        current_x = x
        last_end = 0

        for start, end, emote in emote_positions:
            # Draw text before emote
            if start > last_end:
                segment = text[last_end:start]
                seg_width = fm.horizontalAdvance(segment)
                if is_moderated:
                    self._draw_strikethrough_text(
                        painter, current_x, y, seg_width, line_height, segment, fm
                    )
                else:
                    painter.drawText(
                        current_x,
                        y,
                        seg_width + 10,
                        line_height,
                        ALIGN_LEFT_VCENTER,
                        segment,
                    )
                current_x += seg_width

            # Draw emote
            emote_key = f"emote:{emote.provider}:{emote.id}"
            pixmap = self._emote_cache.get(emote_key)
            if pixmap and not pixmap.isNull():
                smooth = Qt.TransformationMode.SmoothTransformation
                scaled = pixmap.scaledToHeight(emote_height, smooth)
                emote_y = y + (line_height - emote_height) // 2
                painter.drawPixmap(int(current_x), int(emote_y), scaled)
                current_x += scaled.width()
            else:
                emote_text = (
                    text[start:end] if end <= len(text) else emote.name
                )
                seg_width = fm.horizontalAdvance(emote_text)
                painter.drawText(
                    int(current_x),
                    y,
                    seg_width + 4,
                    line_height,
                    ALIGN_LEFT_VCENTER,
                    emote_text,
                )
                current_x += seg_width

            last_end = end

        # Draw remaining text after last emote
        if last_end < len(text):
            segment = text[last_end:]
            seg_width = fm.horizontalAdvance(segment)
            if is_moderated:
                self._draw_strikethrough_text(
                    painter, current_x, y, seg_width, line_height, segment, fm
                )
            else:
                painter.drawText(
                    int(current_x),
                    y,
                    seg_width + 10,
                    line_height,
                    ALIGN_LEFT_VCENTER,
                    segment,
                )

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
        if is_moderated:
            self._draw_strikethrough_text(
                painter, x, y, available_width, line_height, text, fm
            )
        else:
            elided = fm.elidedText(
                text, Qt.TextElideMode.ElideNone, available_width * 3
            )
            painter.drawText(
                x, y, available_width, line_height * 3, ALIGN_WRAP, elided
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
        """Calculate the size needed for a message."""
        message: ChatMessage | None = index.data(MessageRole)
        if not message:
            return QSize(option.rect.width(), 24)

        padding_v = self.settings.line_spacing
        font = option.font
        font.setPointSize(self.settings.font_size)
        fm = QFontMetrics(font)

        badge_size = self._get_badge_size(fm)
        emote_height = self._get_emote_height(fm)
        line_height = max(fm.height(), badge_size) + padding_v

        # Calculate total text width to determine line wrapping
        rect_width = option.rect.width()
        if rect_width <= 0:
            # Fallback: use parent viewport width when option rect is unset
            parent = self.parent()
            if parent and hasattr(parent, "viewport"):
                rect_width = parent.viewport().width()
            else:
                rect_width = 400
        available_width = max(rect_width - PADDING_H * 2, 200)

        # Prefix width (timestamp + badges + username)
        prefix_width = 0
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            prefix_width += QFontMetrics(ts_font).horizontalAdvance("00:00") + TIMESTAMP_PADDING
        if self.settings.show_badges and message.user.badges:
            badge_count = len(message.user.badges)
            if not self.settings.show_mod_badges:
                badge_count = sum(
                    1
                    for b in message.user.badges
                    if b.name not in MOD_BADGE_NAMES
                )
            prefix_width += badge_count * (badge_size + BADGE_SPACING)

        bold_font = QFont(font)
        bold_font.setBold(True)
        suffix = ": " if not message.is_action else " "
        name_text = message.user.display_name + suffix
        prefix_width += QFontMetrics(bold_font).horizontalAdvance(name_text)

        # Text width
        text_width = fm.horizontalAdvance(message.text)

        # Calculate number of lines needed
        content_width = available_width - prefix_width
        if content_width <= 0:
            content_width = available_width

        lines = 1
        if text_width > content_width:
            lines = max(1, int(text_width / content_width) + 1)

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
        return QSize(available_width, height)

    def helpEvent(  # noqa: N802
        self, event: QHelpEvent, view: QAbstractItemView,
        option: QStyleOptionViewItem, index: QModelIndex,
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
                "twitch": "Twitch", "kick": "Kick",
                "7tv": "7TV", "bttv": "BTTV", "ffz": "FFZ",
            }
            provider = provider_names.get(emote.provider, emote.provider)
            QToolTip.showText(event.globalPos(), f"{emote.name}\n({provider})")
        else:
            QToolTip.hideText()
        return True

    def _get_emote_at_position(self, pos, option: QStyleOptionViewItem, message: ChatMessage):
        """Find which emote (if any) is at the given position."""
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

        # Now walk through text + emotes
        text = message.text
        last_end = 0

        for start, end, emote in message.emote_positions:
            # Text before emote
            if start > last_end:
                segment = text[last_end:start]
                x += fm.horizontalAdvance(segment)

            # Emote rect
            emote_key = f"emote:{emote.provider}:{emote.id}"
            pixmap = self._emote_cache.get(emote_key)
            if pixmap and not pixmap.isNull():
                smooth = Qt.TransformationMode.SmoothTransformation
                scaled = pixmap.scaledToHeight(emote_height, smooth)
                emote_w = scaled.width()
            else:
                emote_text = text[start:end] if end <= len(text) else emote.name
                emote_w = fm.horizontalAdvance(emote_text)

            emote_y = y + (line_height - emote_height) // 2
            emote_rect = QRect(int(x), int(emote_y), int(emote_w), emote_height)
            if emote_rect.contains(pos):
                return emote
            x += emote_w
            last_end = end

        return None

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
