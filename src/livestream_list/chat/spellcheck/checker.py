"""Spellcheck wrapper around pyspellchecker."""

import logging
import re

from spellchecker import SpellChecker as PySpellChecker

from .dictionary import CustomDictionary

logger = logging.getLogger(__name__)

# Pattern to detect URLs
_URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)


class SpellChecker:
    """Wraps pyspellchecker with custom dictionary and chat-aware skip rules."""

    def __init__(self, dictionary: CustomDictionary | None = None) -> None:
        self._spell = PySpellChecker()
        self._dict = dictionary or CustomDictionary()

    @property
    def dictionary(self) -> CustomDictionary:
        return self._dict

    def _should_skip(self, word: str) -> bool:
        """Return True if the word should never be flagged."""
        if not word or len(word) <= 1:
            return True
        # Mentions, emote triggers, commands
        if word[0] in ("@", ":", "!"):
            return True
        # All digits
        if word.isdigit():
            return True
        # All caps (LOL, GG, LMAO, etc.)
        if word.isupper():
            return True
        # URLs
        if "://" in word or word.lower().startswith("www."):
            return True
        # Custom dictionary (emotes, usernames, user words)
        if self._dict.contains(word):
            return True
        return False

    def check_word(self, word: str) -> bool:
        """Return True if the word is correctly spelled or whitelisted."""
        if self._should_skip(word):
            return True
        return len(self._spell.unknown([word])) == 0

    def check_text(self, text: str) -> list[tuple[int, int, str]]:
        """Check text and return misspelled word ranges.

        Returns list of (start_pos, end_pos, word) for each misspelled word.
        """
        results: list[tuple[int, int, str]] = []
        if not text:
            return results

        # Find URL spans to exclude
        url_spans: list[tuple[int, int]] = []
        for m in _URL_RE.finditer(text):
            url_spans.append((m.start(), m.end()))

        # Find word boundaries
        i = 0
        length = len(text)
        while i < length:
            # Skip non-word characters
            if not text[i].isalpha() and text[i] not in ("@", ":", "!"):
                i += 1
                continue

            # Find word start
            start = i
            # For @/:!/ prefix, include it in the word for skip detection
            if text[i] in ("@", ":", "!"):
                i += 1
                if i >= length or not text[i].isalpha():
                    continue

            # Find word end (allow apostrophes within words like "don't")
            while i < length and (text[i].isalpha() or text[i] == "'"):
                i += 1

            word = text[start:i]

            # Skip if inside a URL span
            in_url = False
            for url_start, url_end in url_spans:
                if start >= url_start and i <= url_end:
                    in_url = True
                    break
            if in_url:
                continue

            if not self.check_word(word):
                # For words with prefix chars, only underline the alpha part
                actual_start = start
                if text[start] in ("@", ":", "!"):
                    actual_start = start + 1
                results.append((actual_start, i, text[actual_start:i]))

        return results

    def get_suggestions(self, word: str, max_count: int = 5) -> list[str]:
        """Get correction suggestions for a misspelled word."""
        candidates = self._spell.candidates(word)
        if not candidates:
            return []
        # Sort by edit distance (candidates already ranked by the library)
        suggestions = sorted(
            candidates, key=lambda c: self._spell.word_usage_frequency(c) or 0, reverse=True
        )
        return suggestions[:max_count]

    def get_best_correction(self, word: str) -> str | None:
        """Get the single best correction for a word."""
        correction = self._spell.correction(word)
        if correction and correction != word.lower():
            return correction
        return None

    def get_confident_correction(self, word: str) -> str | None:
        """Get a correction only when confidence is high.

        Returns the top candidate if:
        - Only 1 candidate exists (unambiguous), or
        - Top candidate frequency is >= 5x the second candidate.
        Returns None if ambiguous or no candidates.
        """
        if self._should_skip(word):
            return None
        candidates = self._spell.candidates(word)
        if not candidates:
            return None
        # Remove the original word itself from candidates
        candidates = {c for c in candidates if c != word.lower()}
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates.pop()
        # Sort by frequency descending
        ranked = sorted(
            candidates,
            key=lambda c: self._spell.word_usage_frequency(c) or 0,
            reverse=True,
        )
        freq_top = self._spell.word_usage_frequency(ranked[0]) or 0
        freq_second = self._spell.word_usage_frequency(ranked[1]) or 0
        if freq_second > 0 and freq_top / freq_second >= 5.0:
            return ranked[0]
        return None

    def add_words(self, words: set[str]) -> None:
        """Whitelist words in the custom dictionary (emotes/usernames)."""
        self._dict.set_emote_names(words)
