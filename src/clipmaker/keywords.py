"""Pick the words to emphasise in captions, and an optional emoji-style symbol for them.

At most one keyword per caption line, so emphasis stays meaningful. A word qualifies
if it is a number, a strong/emotional word, or a hook word (see highlights.py); ties
go to the higher class, then the longer word. Symbols come from glyphs that DejaVu
Sans has (libass can't draw colour-bitmap emoji fonts), coloured in the ASS file. D-009.
"""
from __future__ import annotations

import re

from clipmaker.highlights import NUMBER_WORDS, STRONG_WORDS

# single-token strong words (multi-word phrases like "hard way" are skipped here)
_STRONG = {w for w in STRONG_WORDS if " " not in w}
_HOOKISH = {"secret", "truth", "problem", "mistake", "rule", "trick", "key", "biggest", "reason",
            "stop", "nobody", "most", "everything", "nothing", "best", "worst", "free", "money",
            "הסוד", "האמת", "הטריק", "הכלל", "הבעיה", "הטעות", "הכי", "כסף", "חינם"}

# (symbol, ASS colour &HBBGGRR) per concept; first match in order wins
SYMBOLS: list[tuple[set[str], str, str]] = [
    ({"love", "loved", "heart", "אוהב", "אוהבת", "אהבה"}, "❤", "&H004040FF"),
    ({"mistake", "mistakes", "fail", "failed", "failure", "wrong", "hate", "never", "quit", "quitting",
      "טעות", "הטעות", "נכשלתי", "אף", "שונא"}, "✗", "&H003030E0"),
    ({"stop", "warning", "problem", "danger", "careful", "תפסיקו", "הבעיה", "זהירות"}, "⚠", "&H0000C8FF"),
    ({"amazing", "incredible", "best", "biggest", "secret", "מדהים", "הסוד", "הכי", "סוד"}, "★", "&H0000D7FF"),
    ({"results", "truth", "rule", "key", "always", "works", "right", "תוצאות", "האמת", "הכלל", "תמיד"},
     "✔", "&H0050D050"),
    ({"fast", "faster", "energy", "power", "quick", "quickly", "now", "trick", "מהר", "הטריק", "עכשיו"},
     "⚡", "&H0000E5FF"),
    ({"happy", "fun", "funny", "smile", "שמח", "כיף"}, "☺", "&H0000E5FF"),
]


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


def keyword_class(word: str) -> int:
    """0 = not a keyword, 1 = hook word, 2 = strong word, 3 = number."""
    w = _norm(word)
    if not w:
        return 0
    if any(ch.isdigit() for ch in w) or w in NUMBER_WORDS:
        return 3
    if w in _STRONG:
        return 2
    if w in _HOOKISH:
        return 1
    return 0


def pick_keyword(words: list[str]) -> int | None:
    """Index of the single word to emphasise in a caption line, or None."""
    best, best_key = None, (0, 0)
    for i, word in enumerate(words):
        cls = keyword_class(word)
        key = (cls, len(_norm(word)))
        if cls and key > best_key:
            best, best_key = i, key
    return best


def symbol_for(word: str) -> tuple[str, str] | None:
    """(symbol, ASS colour) for a keyword, or None if no concept matches."""
    w = _norm(word)
    for vocab, sym, colour in SYMBOLS:
        if w in vocab:
            return sym, colour
    return None
