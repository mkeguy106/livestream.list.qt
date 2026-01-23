"""Emote renderer - resolves message text into render segments."""

from dataclasses import dataclass

from ..models import ChatEmote, ChatMessage


@dataclass
class TextSegment:
    """A text portion of a rendered message."""

    text: str
    color: str | None = None  # Override color (for /me messages)


@dataclass
class EmoteSegment:
    """An emote portion of a rendered message."""

    emote: ChatEmote
    cache_key: str  # Key for looking up in EmoteCache


RenderSegment = TextSegment | EmoteSegment


def resolve_message_segments(
    message: ChatMessage,
    emote_map: dict[str, ChatEmote],
) -> list[RenderSegment]:
    """Resolve a message's text into render segments.

    First uses Twitch-native emote positions (from IRC tags), then
    scans remaining text for third-party emotes (7TV, BTTV, FFZ).

    Args:
        message: The chat message to resolve.
        emote_map: Combined map of emote_name -> ChatEmote (all providers).

    Returns:
        List of TextSegment and EmoteSegment in display order.
    """
    segments: list[RenderSegment] = []
    text = message.text

    # Build a sorted list of emote positions (start, end, emote)
    # Start with Twitch-native positions
    positions: list[tuple[int, int, ChatEmote]] = list(message.emote_positions)

    # Find third-party emotes in the remaining text gaps
    if emote_map:
        occupied = set()
        for start, end, _ in positions:
            for i in range(start, end):
                occupied.add(i)

        # Split text into words and check each against emote map
        words = _split_with_positions(text)
        for word, start, end in words:
            if word in emote_map and start not in occupied:
                # Check no overlap with existing positions
                overlaps = False
                for s, e, _ in positions:
                    if start < e and end > s:
                        overlaps = True
                        break
                if not overlaps:
                    positions.append((start, end, emote_map[word]))

    # Sort by position
    positions.sort(key=lambda x: x[0])

    # Build segments
    last_end = 0
    color = None
    if message.is_action:
        color = message.user.color

    for start, end, emote in positions:
        # Text before this emote
        if start > last_end:
            text_part = text[last_end:start]
            if text_part.strip():
                segments.append(TextSegment(text=text_part, color=color))
            elif text_part:
                segments.append(TextSegment(text=text_part, color=color))

        # Emote segment
        cache_key = f"emote:{emote.provider}:{emote.id}"
        segments.append(EmoteSegment(emote=emote, cache_key=cache_key))
        last_end = end

    # Remaining text
    if last_end < len(text):
        remaining = text[last_end:]
        if remaining:
            segments.append(TextSegment(text=remaining, color=color))

    # If no segments at all, return the full text
    if not segments:
        segments.append(TextSegment(text=text, color=color))

    return segments


def _split_with_positions(text: str) -> list[tuple[str, int, int]]:
    """Split text into words with their start/end positions."""
    words: list[tuple[str, int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        # Skip whitespace
        while i < n and text[i] == " ":
            i += 1
        if i >= n:
            break
        # Find word end
        start = i
        while i < n and text[i] != " ":
            i += 1
        words.append((text[start:i], start, i))
    return words
