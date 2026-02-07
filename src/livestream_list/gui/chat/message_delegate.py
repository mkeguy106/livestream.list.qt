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

from ...chat.emotes.cache import EmoteCache
from ...chat.models import ChatMessage
from ...core.settings import BuiltinChatSettings
from ..theme import ThemeManager, get_theme
from .message_model import MessageRole

# Layout constants (base values, scaled by font size ratio at runtime)
PADDING_H = 8
BADGE_SPACING = 2
USERNAME_SPACING = 4
TIMESTAMP_PADDING = 6  # Small gap after timestamp text
_BASE_FONT_SIZE = 10  # Reference font size for layout constants

# Mod-related badge names
MOD_BADGE_NAMES = {"moderator", "vip", "staff", "admin", "broadcaster"}

# URL detection
URL_RE = re.compile(r'https?://[^\s<>\[\]"\'`)\]]+')

# @mention detection (must be at start of text or preceded by whitespace)
MENTION_RE = re.compile(r'(?:^|(?<=\s))@(\w+)')

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
        self._image_store: EmoteCache | None = None
        self._animation_time_ms: int = 0
        # Cache for sizeHint calculations: (msg_id, width, settings_hash) -> QSize
        self._size_cache: dict[tuple, QSize] = {}
        self._size_cache_max = 2000
        self._scaled_cache: dict[tuple[str, int], QPixmap] = {}
        self._scaled_cache_max = 1200
        self._scaled_animated_cache: dict[tuple[str, int], list[QPixmap]] = {}
        self._scaled_animated_cache_max = 400
        # Theme colors (updated via apply_theme)
        self._load_theme_colors()

    def _load_theme_colors(self) -> None:
        """Load colors from the current theme and settings."""
        theme = get_theme()
        self._url_color = QColor(theme.chat_url)
        self._url_color_selected = QColor(theme.chat_url_selected)
        self._system_message_color = QColor(theme.chat_system_message)
        self._text_muted_color = QColor(theme.text_muted)
        # Get user-customizable colors from settings (dark/light mode aware)
        is_dark = ThemeManager.is_dark_mode()
        colors = self.settings.get_colors(is_dark)
        self._alt_row_even = QColor(colors.alt_row_color_even)
        self._alt_row_odd = QColor(colors.alt_row_color_odd)
        self._mention_highlight = QColor(colors.mention_highlight_color)
        self._hype_chat_accent = QColor(218, 165, 32)
        self._mention_accent = QColor(255, 165, 0)

    def apply_theme(self) -> None:
        """Apply theme colors (call when theme changes).

        Note: We intentionally do NOT invalidate the size cache here because
        theme colors don't affect message height - only layout-affecting settings
        (font size, badges, timestamps) require size recalculation.
        """
        self._load_theme_colors()

    def set_image_store(self, store: EmoteCache) -> None:
        """Set the shared image store."""
        self._image_store = store
        self._scaled_cache.clear()
        self._scaled_animated_cache.clear()

    def set_animated_cache(self, cache: dict[str, list[QPixmap]]) -> None:
        """Legacy no-op: animated frames are now fetched from the image store."""
        self._scaled_cache.clear()
        self._scaled_animated_cache.clear()

    def set_animation_frame(self, elapsed_ms: int) -> None:
        """Set the current global animation time (ms)."""
        self._animation_time_ms = elapsed_ms

    def invalidate_size_cache(self) -> None:
        """Clear the sizeHint cache when settings change or on resize."""
        self._size_cache.clear()

    def _get_settings_hash(self) -> tuple:
        """Return a tuple of settings that affect message sizing."""
        return (
            self.settings.font_size,
            self.settings.line_spacing,
            self.settings.show_timestamps,
            self.settings.timestamp_format,
            self.settings.show_badges,
            self.settings.show_mod_badges,
            self.settings.show_emotes,
        )

    def _get_emote_height(self, fm: QFontMetrics) -> int:
        """Get emote height scaled to font size."""
        return int(fm.height() * 1.3)

    def _current_scale(self, painter: QPainter) -> float:
        try:
            return float(painter.device().devicePixelRatioF())
        except Exception:
            return 1.0

    def _current_scale_from_option(self, option: QStyleOptionViewItem) -> float:
        widget = option.widget
        if widget is not None:
            try:
                return float(widget.devicePixelRatioF())
            except Exception:
                return 1.0
        return 1.0

    def _get_image_ref_for_scale(self, emote, scale: float):
        if not self._image_store or not getattr(emote, "image_set", None):
            return None
        image_set = emote.image_set.bind(self._image_store)
        emote.image_set = image_set
        return image_set.get_image_or_loaded(scale=scale)

    def _get_image_ref(self, emote, painter: QPainter):
        return self._get_image_ref_for_scale(emote, self._current_scale(painter))

    def _get_badge_image_ref_for_scale(self, badge, scale: float):
        if not self._image_store or not getattr(badge, "image_set", None):
            return None
        image_set = badge.image_set.bind(self._image_store)
        badge.image_set = image_set
        return image_set.get_image_or_loaded(scale=scale)

    def _get_loaded_pixmap(self, image_ref) -> QPixmap | None:
        if not image_ref:
            return None
        frames = image_ref.store.get_frames(image_ref.key)
        if frames:
            return frames[0]
        if image_ref.is_loaded():
            pixmap = image_ref.store.get(image_ref.key)
            if pixmap and not pixmap.isNull():
                return pixmap
        return None

    @staticmethod
    def _scaled_width(pixmap: QPixmap, height: int) -> int:
        if pixmap.isNull() or pixmap.height() <= 0 or height <= 0:
            return 0
        return max(1, int(round(pixmap.width() * (height / pixmap.height()))))

    def _get_scaled_pixmap(self, key: str, pixmap: QPixmap, height: int) -> QPixmap:
        """Return a cached scaled pixmap for the given key/height."""
        cache_key = (key, height)
        cached = self._scaled_cache.get(cache_key)
        if cached and not cached.isNull():
            return cached
        smooth = Qt.TransformationMode.SmoothTransformation
        scaled = pixmap.scaledToHeight(height, smooth)
        if len(self._scaled_cache) >= self._scaled_cache_max:
            keys = list(self._scaled_cache.keys())
            for k in keys[: len(keys) // 4]:
                del self._scaled_cache[k]
        self._scaled_cache[cache_key] = scaled
        return scaled

    def _get_scaled_animated_frames(
        self, key: str, frames: list[QPixmap], height: int
    ) -> list[QPixmap]:
        """Return cached scaled frames for an animated emote."""
        cache_key = (key, height)
        cached = self._scaled_animated_cache.get(cache_key)
        if cached:
            return cached
        smooth = Qt.TransformationMode.SmoothTransformation
        scaled = [frame.scaledToHeight(height, smooth) for frame in frames]
        if len(self._scaled_animated_cache) >= self._scaled_animated_cache_max:
            keys = list(self._scaled_animated_cache.keys())
            for k in keys[: len(keys) // 4]:
                del self._scaled_animated_cache[k]
        self._scaled_animated_cache[cache_key] = scaled
        return scaled

    def _select_animated_frame(self, image_ref, frames: list[QPixmap]) -> QPixmap | None:
        """Select the correct animation frame based on per-frame delays."""
        if not frames:
            return None
        delays = None
        if self._image_store and image_ref:
            delays = self._image_store.get_frame_delays(image_ref.key)
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
        # Fallback: fixed 50ms cadence
        idx = (self._animation_time_ms // 50) % len(frames)
        return frames[idx]

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
            # System message background - semi-transparent purple
            sys_bg = QColor(self._system_message_color)
            sys_bg.setAlpha(40)
            painter.fillRect(option.rect, sys_bg)
        elif message.is_hype_chat:
            hype_bg = QColor(self._hype_chat_accent)
            hype_bg.setAlpha(30)
            painter.fillRect(option.rect, hype_bg)
            # Left accent bar for hype chat
            accent_rect = QRect(option.rect.x(), option.rect.y(), 3, option.rect.height())
            painter.fillRect(accent_rect, self._hype_chat_accent)
        elif message.is_mention:
            painter.fillRect(option.rect, self._mention_highlight)
            # Left accent bar for mentions
            accent_rect = QRect(option.rect.x(), option.rect.y(), 3, option.rect.height())
            painter.fillRect(accent_rect, self._mention_accent)
        elif self.settings.show_alternating_rows:
            if index.row() % 2 == 0:
                color = self._alt_row_even
            else:
                color = self._alt_row_odd
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
            ts_text = message.timestamp.astimezone().strftime(self.settings.ts_strftime)
            painter.setPen(highlight_color if is_selected else self._text_muted_color)
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
            painter.setPen(highlight_color if is_selected else self._system_message_color)
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

        # Reply context (shown above the message on its own line)
        if message.reply_parent_display_name:
            reply_font = QFont(font)
            reply_font.setItalic(True)
            reply_font.setPointSize(max(self.settings.font_size - 1, 4))
            painter.setFont(reply_font)
            painter.setPen(highlight_color if is_selected else self._text_muted_color)
            reply_text = message.reply_parent_text
            if len(reply_text) > 50:
                reply_text = reply_text[:50] + "\u2026"
            reply_str = f"Replying to @{message.reply_parent_display_name}: {reply_text}"
            reply_fm = QFontMetrics(reply_font)
            remaining = available_width - (x - rect.x())
            painter.drawText(x, y, remaining, line_height, ALIGN_LEFT_VCENTER, reply_str)
            y += reply_fm.height() + 2
            x = rect.x()
            painter.setFont(font)

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
                if not badge.image_set or not self._image_store:
                    continue
                image_set = badge.image_set.bind(self._image_store)
                badge.image_set = image_set
                image_ref = image_set.get_image_or_loaded(scale=self._current_scale(painter))
                pixmap = image_ref.pixmap_or_load() if image_ref else None
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
                is_selected=is_selected,
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
                is_selected=is_selected,
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
        is_selected: bool = False,
    ) -> None:
        """Paint text with inline emotes, wrapping at available_width."""
        start_x = x
        current_x = x
        current_y = y
        right_edge = x + available_width
        last_end = 0
        url_ranges = self._get_url_ranges(text)
        mention_ranges = self._get_mention_ranges(text)
        last_emote_rect: QRect | None = None

        for start, end, emote in emote_positions:
            # Draw text before emote (word-by-word wrapping)
            if start > last_end:
                prev_y = current_y
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
                    mention_ranges=mention_ranges,
                    text_offset=last_end,
                    is_selected=is_selected,
                )
                if current_y != prev_y:
                    last_emote_rect = None

            # Draw emote (wrap whole emote if it doesn't fit)
            image_ref = self._get_image_ref(emote, painter)
            frames = image_ref.frames_or_load() if image_ref else None
            if frames and self.settings.animate_emotes:
                key = image_ref.key if image_ref else f"emote:{emote.provider}:{emote.id}"
                scaled_frames = self._get_scaled_animated_frames(key, frames, emote_height)
                pixmap = self._select_animated_frame(image_ref, scaled_frames)
            elif frames:
                key = image_ref.key if image_ref else f"emote:{emote.provider}:{emote.id}"
                scaled_frames = self._get_scaled_animated_frames(key, frames, emote_height)
                pixmap = scaled_frames[0] if scaled_frames else frames[0]
            else:
                pixmap = image_ref.pixmap_or_load() if image_ref else None
            if pixmap and not pixmap.isNull():
                if frames:
                    scaled = pixmap
                else:
                    key = image_ref.key if image_ref else f"emote:{emote.provider}:{emote.id}"
                    scaled = self._get_scaled_pixmap(key, pixmap, emote_height)
                emote_w = scaled.width()
                if emote.zero_width and last_emote_rect is not None:
                    overlay_x = int(
                        last_emote_rect.x() + (last_emote_rect.width() - emote_w) / 2
                    )
                    overlay_y = int(last_emote_rect.y())
                    painter.drawPixmap(overlay_x, overlay_y, scaled)
                    last_end = end
                    continue
                if current_x + emote_w > right_edge and current_x > start_x:
                    current_x = start_x
                    current_y += line_height
                    last_emote_rect = None
                emote_y = current_y + (line_height - emote_height) // 2
                painter.drawPixmap(int(current_x), int(emote_y), scaled)
                last_emote_rect = QRect(int(current_x), int(emote_y), int(emote_w), emote_height)
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
                    is_selected=is_selected,
                )
                last_emote_rect = None

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
                mention_ranges=mention_ranges,
                text_offset=last_end,
                is_selected=is_selected,
            )

    @staticmethod
    def _get_url_ranges(text: str) -> list[tuple[int, int, str]]:
        """Find all URLs in text, returning (start, end, url) tuples."""
        return [(m.start(), m.end(), m.group()) for m in URL_RE.finditer(text)]

    @staticmethod
    def _get_mention_ranges(text: str) -> list[tuple[int, int, str]]:
        """Find all @mentions in text, returning (start, end, username) tuples.

        start/end cover the full '@username' span; username is without '@'.
        """
        return [(m.start(), m.end(), m.group(1)) for m in MENTION_RE.finditer(text)]

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
        mention_ranges: list[tuple[int, int, str]] | None = None,
        text_offset: int = 0,
        is_selected: bool = False,
    ) -> tuple[int, int]:
        """Draw text word-by-word, wrapping to the next line as needed.

        Returns (current_x, current_y) after drawing.
        url_ranges are absolute (start, end, url) tuples in the full message text.
        mention_ranges are absolute (start, end, username) tuples in the full message text.
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

            # Check if word is an @mention
            in_mention = False
            if not in_url and mention_ranges and not is_moderated:
                for m_start, m_end, _ in mention_ranges:
                    if abs_word_start < m_end and abs_word_end > m_start:
                        in_mention = True
                        break

            if draw_word:
                if in_url:
                    saved_pen = painter.pen()
                    url_color = self._url_color_selected if is_selected else self._url_color
                    painter.setPen(url_color)
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
                elif in_mention:
                    saved_pen = painter.pen()
                    mention_color = (
                        self._url_color_selected if is_selected else self._url_color
                    )
                    painter.setPen(mention_color)
                    painter.drawText(
                        int(current_x),
                        int(current_y),
                        word_width + 2,
                        line_height,
                        ALIGN_LEFT_VCENTER,
                        draw_word,
                    )
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
        is_selected: bool = False,
    ) -> None:
        """Paint text with word wrapping."""
        right_edge = x + available_width
        url_ranges = self._get_url_ranges(text)
        mention_ranges = self._get_mention_ranges(text)
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
            mention_ranges=mention_ranges,
            text_offset=0,
            is_selected=is_selected,
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
        scale = self._current_scale_from_option(option)

        badge_size = self._get_badge_size(fm)
        emote_height = self._get_emote_height(fm)
        line_height = max(fm.height(), badge_size) + padding_v

        # Prefix width (timestamp + badges + username)
        prefix_width = 0
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            prefix_width += QFontMetrics(ts_font).horizontalAdvance(
                self.settings.ts_measure_text
            ) + TIMESTAMP_PADDING
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
                message.text,
                message.emote_positions,
                content_width,
                fm,
                emote_height,
                scale,
            )
        else:
            text_width = fm.horizontalAdvance(message.text)
            lines = 1
            if text_width > content_width:
                # Simulate word wrapping
                lines = self._compute_wrapped_lines(message.text, content_width, fm)

        # Reply context needs an extra line
        extra_reply_height = 0
        if message.reply_parent_display_name:
            reply_font = QFont(font)
            reply_font.setItalic(True)
            reply_font.setPointSize(max(self.settings.font_size - 1, 4))
            extra_reply_height = QFontMetrics(reply_font).height() + 2

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
            line_height * (lines + extra_lines) + extra_reply_height + padding_v * 2,
            emote_height + padding_v * 2,
        )
        result = QSize(available_width, height)

        # Store in cache (with size limit - evict oldest 25% when full)
        if len(self._size_cache) >= self._size_cache_max:
            keys = list(self._size_cache.keys())
            for k in keys[: len(keys) // 4]:
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
        scale = self._current_scale_from_option(option)

        badge_size = self._get_badge_size(fm)
        line_height = max(fm.height(), badge_size)

        # Skip timestamp
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            x += QFontMetrics(ts_font).horizontalAdvance(
                self.settings.ts_measure_text
            ) + TIMESTAMP_PADDING

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

        # Skip reply context line
        if message.reply_parent_display_name:
            reply_font = QFont(font)
            reply_font.setItalic(True)
            reply_font.setPointSize(max(self.settings.font_size - 1, 4))
            y += QFontMetrics(reply_font).height() + 2
            x = rect.x()

        # Skip badges - must match paint logic for consistent positioning
        if message.user.badges and (self.settings.show_badges or self.settings.show_mod_badges):
            for badge in message.user.badges:
                is_mod = badge.name in MOD_BADGE_NAMES
                if not self.settings.show_badges and not is_mod:
                    continue
                if not self.settings.show_mod_badges and is_mod:
                    continue
                # Only advance x if pixmap exists (matches paint behavior)
                image_ref = self._get_badge_image_ref_for_scale(badge, scale)
                pixmap = self._get_loaded_pixmap(image_ref)
                if pixmap and not pixmap.isNull():
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
        scale = self._current_scale_from_option(option)

        badge_size = self._get_badge_size(fm)
        emote_height = self._get_emote_height(fm)
        line_height = max(fm.height(), badge_size)

        # Skip timestamp
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            x += QFontMetrics(ts_font).horizontalAdvance(
                self.settings.ts_measure_text
            ) + TIMESTAMP_PADDING

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

        # Skip reply context line
        if message.reply_parent_display_name:
            reply_font = QFont(font)
            reply_font.setItalic(True)
            reply_font.setPointSize(max(self.settings.font_size - 1, 4))
            y += QFontMetrics(reply_font).height() + 2
            x = rect.x()

        # Skip badges - must match paint logic for consistent positioning
        if message.user.badges and (self.settings.show_badges or self.settings.show_mod_badges):
            for badge in message.user.badges:
                is_mod = badge.name in MOD_BADGE_NAMES
                if not self.settings.show_badges and not is_mod:
                    continue
                if not self.settings.show_mod_badges and is_mod:
                    continue
                # Only advance x if pixmap exists (matches paint behavior)
                image_ref = self._get_badge_image_ref_for_scale(badge, scale)
                pixmap = self._get_loaded_pixmap(image_ref)
                if pixmap and not pixmap.isNull():
                    x += badge_size + BADGE_SPACING

        # Skip username
        bold_font = QFont(font)
        bold_font.setBold(True)
        suffix = " " if message.is_action else ": "
        name_text = message.user.display_name + suffix
        x += QFontMetrics(bold_font).horizontalAdvance(name_text)

        # Walk through text + emotes with wrapping logic
        # start_x must be x (after badges+username) to match paint behavior
        text = message.text
        last_end = 0
        start_x = x
        current_x = x
        current_y = y
        available_width = rect.width()
        right_edge = rect.x() + available_width
        last_emote_rect: QRect | None = None

        for start, end, emote in message.emote_positions:
            # Advance past text before emote (with wrapping)
            if start > last_end:
                prev_y = current_y
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
                if current_y != prev_y:
                    last_emote_rect = None

            # Emote rect (with wrapping)
            image_ref = self._get_image_ref_for_scale(emote, scale)
            pixmap = self._get_loaded_pixmap(image_ref)
            if pixmap and not pixmap.isNull():
                emote_w = self._scaled_width(pixmap, emote_height)
            else:
                emote_text = text[start:end] if end <= len(text) else emote.name
                emote_w = fm.horizontalAdvance(emote_text)

            # Wrap emote if needed
            if emote.zero_width and last_emote_rect is not None:
                overlay_x = int(
                    last_emote_rect.x() + (last_emote_rect.width() - emote_w) / 2
                )
                emote_rect = QRect(
                    int(overlay_x), int(last_emote_rect.y()),
                    int(emote_w), emote_height,
                )
                if emote_rect.contains(pos):
                    return emote
                last_end = end
                continue
            if current_x + emote_w > right_edge and current_x > start_x:
                current_x = start_x
                current_y += line_height
                last_emote_rect = None

            emote_y_pos = current_y + (line_height - emote_height) // 2
            emote_rect = QRect(int(current_x), int(emote_y_pos), int(emote_w), emote_height)
            if emote_rect.contains(pos):
                return emote
            current_x += emote_w
            last_emote_rect = emote_rect
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
        scale = self._current_scale_from_option(option)

        badge_size = self._get_badge_size(fm)
        line_height = max(fm.height(), badge_size)

        # Skip timestamp
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            x += QFontMetrics(ts_font).horizontalAdvance(
                self.settings.ts_measure_text
            ) + TIMESTAMP_PADDING

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

        # Skip reply context line
        if message.reply_parent_display_name:
            reply_font = QFont(font)
            reply_font.setItalic(True)
            reply_font.setPointSize(max(self.settings.font_size - 1, 4))
            y += QFontMetrics(reply_font).height() + 2
            x = rect.x()

        # Skip badges - must match paint logic for consistent positioning
        if message.user.badges and (self.settings.show_badges or self.settings.show_mod_badges):
            for badge in message.user.badges:
                is_mod = badge.name in MOD_BADGE_NAMES
                if not self.settings.show_badges and not is_mod:
                    continue
                if not self.settings.show_mod_badges and is_mod:
                    continue
                # Only advance x if pixmap exists (matches paint behavior)
                image_ref = self._get_badge_image_ref_for_scale(badge, scale)
                pixmap = self._get_loaded_pixmap(image_ref)
                if pixmap and not pixmap.isNull():
                    x += badge_size + BADGE_SPACING

        # Skip username
        bold_font = QFont(font)
        bold_font.setBold(True)
        suffix = " " if message.is_action else ": "
        name_text = message.user.display_name + suffix
        x += QFontMetrics(bold_font).horizontalAdvance(name_text)

        # Walk through text with wrapping, checking URL word positions
        # start_x must be x (after badges+username) to match paint behavior
        text = message.text
        start_x = x
        current_x = x
        current_y = y
        right_edge = rect.x() + rect.width()
        has_base_emote = False

        if message.emote_positions:
            # Walk through text segments between emotes
            last_end = 0
            for em_start, em_end, emote in message.emote_positions:
                if em_start > last_end:
                    segment = text[last_end:em_start]
                    prev_y = current_y
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
                    if current_y != prev_y:
                        has_base_emote = False
                # Skip emote
                emote_height = self._get_emote_height(fm)
                image_ref = self._get_image_ref_for_scale(emote, scale)
                pixmap = self._get_loaded_pixmap(image_ref)
                if pixmap and not pixmap.isNull():
                    emote_w = self._scaled_width(pixmap, emote_height)
                else:
                    emote_text = text[em_start:em_end] if em_end <= len(text) else emote.name
                    emote_w = fm.horizontalAdvance(emote_text)
                if emote.zero_width and has_base_emote:
                    last_end = em_end
                    continue
                if current_x + emote_w > right_edge and current_x > start_x:
                    current_x = start_x
                    current_y += line_height
                    has_base_emote = False
                current_x += emote_w
                has_base_emote = True
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
        if not message.user.badges:
            return None
        if not (self.settings.show_badges or self.settings.show_mod_badges):
            return None

        padding_v = self.settings.line_spacing
        rect = option.rect.adjusted(PADDING_H, padding_v, -PADDING_H, -padding_v)
        x = rect.x()
        y = rect.y()

        font = option.font
        font.setPointSize(self.settings.font_size)
        fm = QFontMetrics(font)
        scale = self._current_scale_from_option(option)

        badge_size = self._get_badge_size(fm)
        line_height = max(fm.height(), badge_size)

        # Skip timestamp
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            x += QFontMetrics(ts_font).horizontalAdvance(
                self.settings.ts_measure_text
            ) + TIMESTAMP_PADDING

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

        # Skip reply context line
        if message.reply_parent_display_name:
            reply_font = QFont(font)
            reply_font.setItalic(True)
            reply_font.setPointSize(max(self.settings.font_size - 1, 4))
            y += QFontMetrics(reply_font).height() + 2
            x = rect.x()

        # Check each badge
        badge_y = y + (line_height - badge_size) // 2
        for badge in message.user.badges:
            is_mod_badge = badge.name in MOD_BADGE_NAMES
            if not self.settings.show_badges and not is_mod_badge:
                continue
            if not self.settings.show_mod_badges and is_mod_badge:
                continue
            image_ref = self._get_badge_image_ref_for_scale(badge, scale)
            pixmap = self._get_loaded_pixmap(image_ref)
            if not pixmap or pixmap.isNull():
                continue
            badge_rect = QRect(int(x), int(badge_y), badge_size, badge_size)
            if badge_rect.contains(pos):
                return badge
            x += badge_size + BADGE_SPACING

        return None

    def _get_reply_context_rect(
        self, option: QStyleOptionViewItem, message: ChatMessage
    ) -> QRect:
        """Get the bounding rect of the reply context line, if present."""
        if not message.reply_parent_display_name:
            return QRect()

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
            x += QFontMetrics(ts_font).horizontalAdvance(
                self.settings.ts_measure_text
            ) + TIMESTAMP_PADDING

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

        # Reply context line rect
        reply_font = QFont(font)
        reply_font.setItalic(True)
        reply_font.setPointSize(max(self.settings.font_size - 1, 4))
        reply_fm = QFontMetrics(reply_font)
        reply_text = message.reply_parent_text
        if len(reply_text) > 50:
            reply_text = reply_text[:50] + "\u2026"
        reply_str = f"Replying to @{message.reply_parent_display_name}: {reply_text}"
        reply_width = reply_fm.horizontalAdvance(reply_str)
        remaining = rect.width() - (x - rect.x())
        return QRect(int(x), int(y), min(int(reply_width), int(remaining)), reply_fm.height())

    def _get_mention_at_position(
        self, pos, option: QStyleOptionViewItem, message: ChatMessage
    ) -> str | None:
        """Find which @mention (if any) is at the given position.

        Returns the bare username (without @) if hit, else None.
        """
        mention_ranges = self._get_mention_ranges(message.text)
        if not mention_ranges:
            return None

        padding_v = self.settings.line_spacing
        rect = option.rect.adjusted(PADDING_H, padding_v, -PADDING_H, -padding_v)
        x = rect.x()
        y = rect.y()

        font = option.font
        font.setPointSize(self.settings.font_size)
        fm = QFontMetrics(font)
        scale = self._current_scale_from_option(option)

        badge_size = self._get_badge_size(fm)
        line_height = max(fm.height(), badge_size)

        # Skip timestamp
        if self.settings.show_timestamps:
            ts_font = QFont(font)
            ts_font.setPointSize(max(self.settings.font_size - 2, 4))
            x += QFontMetrics(ts_font).horizontalAdvance(
                self.settings.ts_measure_text
            ) + TIMESTAMP_PADDING

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

        # Skip reply context line
        if message.reply_parent_display_name:
            reply_font = QFont(font)
            reply_font.setItalic(True)
            reply_font.setPointSize(max(self.settings.font_size - 1, 4))
            y += QFontMetrics(reply_font).height() + 2
            x = rect.x()

        # Skip badges
        if message.user.badges and (self.settings.show_badges or self.settings.show_mod_badges):
            for badge in message.user.badges:
                is_mod = badge.name in MOD_BADGE_NAMES
                if not self.settings.show_badges and not is_mod:
                    continue
                if not self.settings.show_mod_badges and is_mod:
                    continue
                image_ref = self._get_badge_image_ref_for_scale(badge, scale)
                pixmap = self._get_loaded_pixmap(image_ref)
                if pixmap and not pixmap.isNull():
                    x += badge_size + BADGE_SPACING

        # Skip username
        bold_font = QFont(font)
        bold_font.setBold(True)
        suffix = " " if message.is_action else ": "
        name_text = message.user.display_name + suffix
        x += QFontMetrics(bold_font).horizontalAdvance(name_text)

        # Walk through text checking mention word positions
        text = message.text
        start_x = x
        current_x = x
        current_y = y
        right_edge = rect.x() + rect.width()
        has_base_emote = False

        if message.emote_positions:
            last_end = 0
            for em_start, em_end, emote in message.emote_positions:
                if em_start > last_end:
                    segment = text[last_end:em_start]
                    prev_y = current_y
                    result = self._check_mention_words_at_pos(
                        segment, current_x, current_y, start_x, right_edge,
                        line_height, fm, pos, mention_ranges, last_end,
                    )
                    if result:
                        return result
                    current_x, current_y = self._advance_wrapping_text(
                        segment, current_x, current_y, start_x, right_edge, line_height, fm,
                    )
                    if current_y != prev_y:
                        has_base_emote = False
                # Skip emote
                emote_height = self._get_emote_height(fm)
                image_ref = self._get_image_ref_for_scale(emote, scale)
                pixmap = self._get_loaded_pixmap(image_ref)
                if pixmap and not pixmap.isNull():
                    emote_w = self._scaled_width(pixmap, emote_height)
                else:
                    emote_text = text[em_start:em_end] if em_end <= len(text) else emote.name
                    emote_w = fm.horizontalAdvance(emote_text)
                if emote.zero_width and has_base_emote:
                    last_end = em_end
                    continue
                if current_x + emote_w > right_edge and current_x > start_x:
                    current_x = start_x
                    current_y += line_height
                    has_base_emote = False
                current_x += emote_w
                has_base_emote = True
                last_end = em_end

            if last_end < len(text):
                segment = text[last_end:]
                result = self._check_mention_words_at_pos(
                    segment, current_x, current_y, start_x, right_edge,
                    line_height, fm, pos, mention_ranges, last_end,
                )
                if result:
                    return result
        else:
            result = self._check_mention_words_at_pos(
                text, current_x, current_y, start_x, right_edge,
                line_height, fm, pos, mention_ranges, 0,
            )
            if result:
                return result

        return None

    def _check_mention_words_at_pos(
        self,
        text: str,
        current_x: int,
        current_y: int,
        start_x: int,
        right_edge: int,
        line_height: int,
        fm: QFontMetrics,
        pos,
        mention_ranges: list[tuple[int, int, str]],
        text_offset: int,
    ) -> str | None:
        """Check if pos hits any @mention word in the given text segment."""
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

            for m_start, m_end, username in mention_ranges:
                if abs_word_start < m_end and abs_word_end > m_start:
                    word_rect = QRect(
                        int(current_x), int(current_y), int(word_width), line_height,
                    )
                    if word_rect.contains(pos):
                        return username
                    break

            current_x += word_width
            char_pos += len(draw_word) if i == 0 else 1 + len(word)

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
        scale: float,
    ) -> int:
        """Compute how many lines text+emotes need with word wrapping."""
        lines = 1
        current_x = 0
        last_end = 0
        has_base_emote = False

        for start, end, emote in emote_positions:
            # Text before emote
            if start > last_end:
                segment = text[last_end:start]
                prev_lines = lines
                current_x, lines = self._advance_line_count(
                    segment, current_x, lines, content_width, fm
                )
                if lines != prev_lines:
                    has_base_emote = False

            # Emote width
            image_ref = self._get_image_ref_for_scale(emote, scale)
            pixmap = self._get_loaded_pixmap(image_ref)
            if pixmap and not pixmap.isNull():
                emote_w = self._scaled_width(pixmap, emote_height)
            else:
                emote_text = text[start:end] if end <= len(text) else emote.name
                emote_w = fm.horizontalAdvance(emote_text)

            if emote.zero_width and has_base_emote:
                last_end = end
                continue

            if current_x + emote_w > content_width and current_x > 0:
                lines += 1
                current_x = 0
                has_base_emote = False
            current_x += emote_w
            has_base_emote = True
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
