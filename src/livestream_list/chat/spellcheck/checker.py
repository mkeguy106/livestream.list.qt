"""Spellcheck wrapper around hunspell."""

import logging
import os
import re
from pathlib import Path

import hunspell

from .dictionary import CustomDictionary

logger = logging.getLogger(__name__)

# Pattern to detect URLs
_URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)

# Standard hunspell dictionary search paths
_DICT_SEARCH_PATHS = [
    "/usr/share/hunspell",
    "/usr/share/myspell",
    "/usr/share/myspell/dicts",
    "/app/share/hunspell",  # Flatpak
]


def _find_hunspell_dict(lang: str = "en_US") -> tuple[str, str]:
    """Find hunspell dictionary files (.dic and .aff) on the system.

    Returns (dic_path, aff_path).
    Raises FileNotFoundError if not found.
    """
    search_paths = list(_DICT_SEARCH_PATHS)

    # Also check DICPATH env var
    dicpath = os.environ.get("DICPATH")
    if dicpath:
        search_paths.extend(dicpath.split(os.pathsep))

    for directory in search_paths:
        dic = Path(directory) / f"{lang}.dic"
        aff = Path(directory) / f"{lang}.aff"
        if dic.exists() and aff.exists():
            return str(dic), str(aff)

    raise FileNotFoundError(
        f"Hunspell dictionary '{lang}' not found. "
        f"Install hunspell-en-us (Debian/Ubuntu) or hunspell-en_us (Arch)."
    )


def _damerau_levenshtein(a: str, b: str) -> int:
    """Compute Damerau-Levenshtein distance (transpositions count as 1 edit)."""
    len_a, len_b = len(a), len(b)
    # Use a dict-based DP to handle the transposition case
    d: dict[tuple[int, int], int] = {}
    for i in range(-1, len_a + 1):
        d[i, -1] = i + 1
    for j in range(-1, len_b + 1):
        d[-1, j] = j + 1
    for i in range(len_a):
        for j in range(len_b):
            cost = 0 if a[i] == b[j] else 1
            d[i, j] = min(
                d[i - 1, j] + 1,      # deletion
                d[i, j - 1] + 1,      # insertion
                d[i - 1, j - 1] + cost,  # substitution
            )
            # Transposition
            if i > 0 and j > 0 and a[i] == b[j - 1] and a[i - 1] == b[j]:
                d[i, j] = min(d[i, j], d[i - 2, j - 2] + 1)
    return d[len_a - 1, len_b - 1]


def _load_bundled_words() -> list[str]:
    """Load the bundled adult word list so these words aren't flagged."""
    words: list[str] = []
    # Try importlib.resources first (works for installed packages)
    try:
        from importlib.resources import files

        data_dir = files("livestream_list.chat.spellcheck") / "data"
        adult_path = data_dir / "adult.txt"
        text = adult_path.read_text(encoding="utf-8")
        words = [w.strip() for w in text.splitlines() if w.strip()]
    except Exception:
        # Fallback to __file__-relative path
        try:
            data_file = Path(__file__).parent / "data" / "adult.txt"
            if data_file.exists():
                text = data_file.read_text(encoding="utf-8")
                words = [w.strip() for w in text.splitlines() if w.strip()]
        except Exception:
            pass
    return words


class SpellChecker:
    """Wraps hunspell with custom dictionary and chat-aware skip rules."""

    def __init__(self, dictionary: CustomDictionary | None = None) -> None:
        dic_path, aff_path = _find_hunspell_dict()
        self._spell = hunspell.HunSpell(dic_path, aff_path)
        self._dict = dictionary or CustomDictionary()

        # Load bundled adult words into hunspell runtime dict
        bundled = _load_bundled_words()
        for word in bundled:
            self._spell.add(word)
        if bundled:
            logger.debug("Loaded %d bundled words into hunspell", len(bundled))

        # Sync existing custom dictionary words into hunspell
        for word in self._dict.all_words:
            self._spell.add(word)

        # Register callback so future dictionary additions sync to hunspell
        self._dict.set_on_words_added(self._on_dict_words_added)

    def _on_dict_words_added(self, words: set[str]) -> None:
        """Callback when new words are added to the custom dictionary."""
        for word in words:
            self._spell.add(word)

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
        return self._spell.spell(word)

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
        suggestions = self._spell.suggest(word)
        return suggestions[:max_count]

    def get_best_correction(self, word: str) -> str | None:
        """Get the single best correction for a word."""
        suggestions = self._spell.suggest(word)
        if suggestions and suggestions[0].lower() != word.lower():
            return suggestions[0]
        return None

    def get_confident_correction(self, word: str) -> str | None:
        """Get a correction only when confidence is high.

        Returns a correction if:
        - An apostrophe expansion exists (dont→don't, youre→you're), or
        - Only 1 suggestion exists (unambiguous), or
        - Hunspell's top suggestion is within Damerau-Levenshtein distance 1
          (covers transpositions like teh→the, single-char typos).
        Returns None if no suggestions or the word should be skipped.
        """
        if self._should_skip(word):
            return None
        suggestions = self._spell.suggest(word)
        if not suggestions:
            return None
        # Filter out the original word
        suggestions = [s for s in suggestions if s.lower() != word.lower()]
        if not suggestions:
            return None
        # Apostrophe expansions: dont→don't, youre→you're, wont→won't
        for s in suggestions:
            stripped = s.replace("'", "").replace("\u2019", "")
            if stripped.lower() == word.lower():
                return s
        if len(suggestions) == 1:
            return suggestions[0]
        # Trust hunspell's top-ranked suggestion if it's a close match
        top = suggestions[0]
        if _damerau_levenshtein(word.lower(), top.lower()) <= 1:
            return top
        return None

    def add_words(self, words: set[str]) -> None:
        """Whitelist words in the custom dictionary (emotes/usernames)."""
        self._dict.set_emote_names(words)
