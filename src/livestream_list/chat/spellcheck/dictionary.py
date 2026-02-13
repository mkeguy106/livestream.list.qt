"""Custom dictionary management for spellcheck."""

import logging
from collections.abc import Callable
from pathlib import Path

from ...core.settings import get_data_dir

logger = logging.getLogger(__name__)

DICT_FILENAME = "spellcheck_dictionary.txt"


class CustomDictionary:
    """Three-layer custom dictionary: user words, emote names, usernames.

    User words are persisted to disk. Emote names and usernames are dynamic
    and rebuilt as new data comes in.
    """

    def __init__(self) -> None:
        self._user_words: set[str] = set()
        self._emote_names: set[str] = set()
        self._usernames: set[str] = set()
        self._on_words_added: Callable[[set[str]], None] | None = None
        self._load_user_words()

    def set_on_words_added(self, callback: Callable[[set[str]], None]) -> None:
        """Register a callback invoked when new words are added."""
        self._on_words_added = callback

    @property
    def all_words(self) -> set[str]:
        """Return the union of all custom words (lowercased)."""
        return self._user_words | self._emote_names | self._usernames

    def _dict_path(self) -> Path:
        return get_data_dir() / DICT_FILENAME

    def _load_user_words(self) -> None:
        """Load persisted user words from disk."""
        path = self._dict_path()
        if not path.exists():
            return
        try:
            text = path.read_text(encoding="utf-8")
            self._user_words = {w.strip().lower() for w in text.splitlines() if w.strip()}
        except Exception as e:
            logger.warning(f"Failed to load custom dictionary: {e}")

    def _save_user_words(self) -> None:
        """Persist user words to disk."""
        path = self._dict_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(sorted(self._user_words)), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save custom dictionary: {e}")

    def add_user_word(self, word: str) -> None:
        """Add a word to the user dictionary (persisted)."""
        lower = word.lower()
        if lower not in self._user_words:
            self._user_words.add(lower)
            self._save_user_words()
            if self._on_words_added:
                self._on_words_added({lower})

    def set_emote_names(self, names: set[str]) -> None:
        """Replace the emote name set."""
        new_names = {n.lower() for n in names}
        added = new_names - self._emote_names
        self._emote_names = new_names
        if added and self._on_words_added:
            self._on_words_added(added)

    def add_username(self, name: str) -> None:
        """Add a username to the dynamic dictionary."""
        if name:
            lower = name.lower()
            if lower not in self._usernames:
                self._usernames.add(lower)
                if self._on_words_added:
                    self._on_words_added({lower})

    def contains(self, word: str) -> bool:
        """Check if a word is in any custom dictionary layer."""
        return word.lower() in self.all_words
