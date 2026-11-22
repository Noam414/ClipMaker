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


_LEADING_FILLERS = {"okay", "ok", "so", "um", "uh", "well", "alright", "anyway", "like", "and", "but",
                    "אה", "אממ", "אז", "טוב", "בקיצור", "רגע", "ו"}


def hook_title(text: str, max_words: int = 7) -> str:
    """A short title for the first seconds of a clip: its opening sentence without
    leading filler, cut to `max_words` words (with an ellipsis), first letter capitalised.
    A filler-only opening sentence ("Okay.") is skipped in favour of the next one."""
    words: list[str] = []
    for sentence in re.split(r"(?<=[.?!…])\s+", text.strip()):
        words = sentence.split()
        while words and _norm(words[0]).strip("'") in _LEADING_FILLERS:
            words.pop(0)
        if words:
            break
    if not words:
        return ""
    cut = len(words) > max_words
    words = words[:max_words]
    title = " ".join(words)
    if cut:
        title = title.rstrip(",;:-") + "…"
    elif title[-1] == ".":
        title = title[:-1]
    return title[0].upper() + title[1:]


def punch_ins(captions, min_gap: float = 4.0, min_len: float = 0.4, max_len: float = 2.0,
              min_class: int = 2) -> list[tuple[float, float]]:
    """(start, end) windows, on the caption timeline, for a zoom punch-in: from a strong
    keyword (number or strong word) until its caption line ends, spaced at least
    `min_gap` seconds apart. `captions` are captions.Caption objects. D-011."""
    out: list[tuple[float, float]] = []
    for c in captions:
        words = c.words or [(c.start, c.end, w) for w in c.text.split()]
        i = pick_keyword([w for _, _, w in words])
        if i is None or keyword_class(words[i][2]) < min_class:
            continue
        a = max(words[i][0], c.start)
        b = min(c.end, a + max_len)
        if b - a < min_len or (out and a - out[-1][0] < min_gap):
            continue
        out.append((round(a, 3), round(b, 3)))
    return out


PUNCH_IN_SCALE = 1.12


def zoom_expression(windows: list[tuple[float, float]], scale: float = PUNCH_IN_SCALE) -> str | None:
    """ffmpeg expression of `it` (output seconds): `scale` inside the windows, else 1."""
    if not windows:
        return None
    inside = "+".join(f"between(it,{a:.3f},{b:.3f})" for a, b in windows)
    return f"if(gt({inside},0),{scale:g},1)"
