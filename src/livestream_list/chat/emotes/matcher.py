"""Third-party emote matcher (boundary-aware)."""

from __future__ import annotations

from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import ChatEmote
else:
    ChatEmote = object  # type: ignore[misc]


def find_third_party_emotes(
    text: str,
    emote_map: dict[str, ChatEmote],
    claimed_ranges: Iterable[tuple[int, int]] | None = None,
) -> list[tuple[int, int, ChatEmote]]:
    """Return new third-party emote positions within text."""
    if not text or not emote_map:
        return []

    claimed = list(claimed_ranges or [])
    new_positions: list[tuple[int, int, ChatEmote]] = []

    def overlaps(start: int, end: int) -> bool:
        for s, e in claimed:
            if start < e and end > s:
                return True
        return False

    def is_word_char(ch: str) -> bool:
        return ch.isalnum() or ch == "_"

    def try_add(start: int, end: int, name: str) -> bool:
        emote = emote_map.get(name)
        if not emote:
            return False
        if overlaps(start, end):
            return False
        new_positions.append((start, end, emote))
        claimed.append((start, end))
        return True

    def best_trimmed_match(segment: str, seg_start: int) -> tuple[int, int, str] | None:
        trim_chars = "[](){}<>\"'`"
        left_max = 0
        right_max = 0
        while left_max < len(segment) and segment[left_max] in trim_chars:
            left_max += 1
        while right_max < len(segment) - left_max and segment[len(segment) - 1 - right_max] in trim_chars:
            right_max += 1
        best: tuple[int, int, str] | None = None
        best_len = 0
        for l in range(left_max + 1):
            for r in range(right_max + 1):
                if l == 0 and r == 0:
                    continue
                if l + r >= len(segment):
                    continue
                candidate = segment[l : len(segment) - r]
                if not candidate:
                    continue
                if candidate in emote_map and len(candidate) > best_len:
                    best_len = len(candidate)
                    best = (seg_start + l, seg_start + len(segment) - r, candidate)
        return best

    i = 0
    n = len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        run_start = i
        while i < n and not text[i].isspace():
            i += 1
        run_end = i
        token = text[run_start:run_end]
        if "http://" in token or "https://" in token:
            continue

        segments: list[tuple[int, int, bool]] = []
        seg_start = run_start
        seg_kind = is_word_char(text[seg_start])
        for j in range(run_start + 1, run_end):
            kind = is_word_char(text[j])
            if kind != seg_kind:
                segments.append((seg_start, j, seg_kind))
                seg_start = j
                seg_kind = kind
        segments.append((seg_start, run_end, seg_kind))

        idx = 0
        while idx < len(segments):
            if idx + 2 < len(segments):
                start = segments[idx][0]
                end = segments[idx + 2][1]
                combined = text[start:end]
                if try_add(start, end, combined):
                    idx += 3
                    continue
            if idx + 1 < len(segments):
                start = segments[idx][0]
                end = segments[idx + 1][1]
                combined = text[start:end]
                if try_add(start, end, combined):
                    idx += 2
                    continue

            seg_start, seg_end, is_word = segments[idx]
            seg_text = text[seg_start:seg_end]

            if (
                is_word
                and idx + 2 < len(segments)
                and not segments[idx + 1][2]
                and text[segments[idx + 1][0] : segments[idx + 1][1]] in {"'", "’"}
                and segments[idx + 2][2]
            ):
                idx += 1
                continue

            if is_word:
                try_add(seg_start, seg_end, seg_text)
            else:
                matched = try_add(seg_start, seg_end, seg_text)
                if not matched:
                    trimmed = best_trimmed_match(seg_text, seg_start)
                    if trimmed:
                        try_add(trimmed[0], trimmed[1], trimmed[2])
            idx += 1

    return new_positions
