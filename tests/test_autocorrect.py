"""Tests for autocorrect's decision rules.

Autocorrect rewrites the user's text silently, so every rule here exists to
stop it doing that when it shouldn't. The decision logic is pure and takes
suggestions as input, so these run without a spellcheck backend and without Qt.
"""

from livestream_list.chat.spellcheck.checker import caret_inside, choose_confident_correction


# --- caret guard ------------------------------------------------------------
# Autocorrect used to fire on a word while the user was editing it: they moved
# the cursor back into a word, deleted a character, and the shortened word was
# rewritten out from under them with the caret flung past it.


def test_caret_before_word_is_outside():
    assert not caret_inside(9, 18, 8)


def test_caret_after_word_is_outside():
    assert not caret_inside(9, 18, 19)


def test_caret_at_word_start_counts_as_inside():
    assert caret_inside(9, 18, 9)


def test_caret_in_middle_of_word_is_inside():
    assert caret_inside(9, 18, 14)


def test_caret_at_word_end_counts_as_inside():
    # The user has backed up to just past the word to edit it; treat that as
    # inside so a deletion there is not immediately overwritten.
    assert caret_inside(9, 18, 18)


# --- confidence rules -------------------------------------------------------


def test_transposition_is_corrected():
    assert choose_confident_correction("teh", ["the", "eh", "ten"]) == "the"


def test_single_unambiguous_suggestion_is_corrected():
    assert choose_confident_correction("helo", ["hello"]) == "hello"


def test_apostrophe_expansion_is_corrected():
    assert choose_confident_correction("dont", ["done", "donut", "don't"]) == "don't"


def test_distant_suggestion_is_not_corrected():
    assert choose_confident_correction("xyzq", ["hello", "world", "cat"]) is None


def test_no_suggestions_means_no_correction():
    assert choose_confident_correction("asdfgh", []) is None


# --- names ------------------------------------------------------------------
# A capitalized word is a name far more often than it is a typo. Silently
# turning "Kaydop" into "Gaydo" is much worse than leaving a typo underlined.


def test_capitalized_name_is_not_corrected():
    assert choose_confident_correction("Darline", ["Darlene"]) is None


def test_capitalized_streamer_name_is_not_corrected():
    assert choose_confident_correction("Kaydop", ["Gaydo"]) is None


def test_capitalized_word_still_gets_apostrophe_expansion():
    # The high-confidence rule survives: names essentially never collide with
    # an apostrophe-less contraction.
    assert choose_confident_correction("Dont", ["Don't", "Done"]) == "Don't"


def test_lowercase_typo_is_still_corrected():
    # The capitalization rule must not disable ordinary autocorrect.
    assert choose_confident_correction("wnoderful", ["wonderful"]) == "wonderful"


def test_all_caps_word_is_not_corrected():
    assert choose_confident_correction("GG", ["Gg"]) is None


# --- multi-word replacements ------------------------------------------------
# Splitting a word in two is the most disruptive thing autocorrect can do, and
# it is never what a chat user wants.


def test_suggestion_containing_a_space_is_rejected():
    assert choose_confident_correction("arejay", ["are jay"]) is None


def test_word_falls_through_to_next_suggestion_is_not_assumed():
    # Rejecting the multi-word top suggestion must not silently promote a
    # lower-ranked, less-confident one.
    assert choose_confident_correction("arejay", ["are jay", "areaway"]) is None
