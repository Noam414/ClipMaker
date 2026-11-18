"""Burned-in captions: group words into short lines and write an ASS file for libass.

Basic style for M1 (bold white text, black outline, lower third). Animated
word-by-word styles come in M4. Hebrew is written in logical order; libass
(built with fribidi) lays it out right-to-left.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from clipmaker.segment import SENTENCE_END

FONT = "DejaVu Sans"  # has Latin + Hebrew glyphs; libass falls back if missing
FONT_SIZE = 84
MARGIN_V = 420


@dataclass
class Caption:
    start: float
    end: float
    text: str


def clip_words(words: list[dict], start: float, end: float, tol: float = 0.05) -> list[dict]:
    return [w for w in words if w["start"] >= start - tol and w["end"] <= end + tol]


def group_words(words: list[dict], offset: float, max_words: int = 4, max_chars: int = 22,
                hold_gap: float = 0.5) -> list[Caption]:
    """Short caption lines; break at sentence ends. Times are relative to offset."""
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        text_len = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
        if cur and (len(cur) >= max_words or text_len > max_chars):
            groups.append(cur)
            cur = []
        cur.append(w)
        if w["word"].rstrip("\"'”’)").endswith(SENTENCE_END):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    caps = [Caption(max(g[0]["start"] - offset, 0.0), max(g[-1]["end"] - offset, 0.0),
                    " ".join(x["word"] for x in g)) for g in groups]
    # close small gaps so captions don't flicker between lines
    for a, b in zip(caps, caps[1:]):
        if 0 <= b.start - a.end < hold_gap:
            a.end = b.start
    return caps


def _ts(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def write_ass(captions: list[Caption], path: Path, width: int = 1080, height: int = 1920) -> Path:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT},{FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,80,80,{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{_ts(c.start)},{_ts(c.end)},Default,,0,0,0,,{_escape(c.text)}" for c in captions]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path
