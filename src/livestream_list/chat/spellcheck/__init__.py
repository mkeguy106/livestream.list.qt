"""Spellcheck support for chat input."""

try:
    from .checker import SpellChecker
except ImportError:
    SpellChecker = None  # type: ignore[assignment,misc]

from .dictionary import CustomDictionary

__all__ = ["SpellChecker", "CustomDictionary"]
