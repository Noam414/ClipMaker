"""Burned-in captions: group words into short lines and write an ASS file for libass.

Styles (see STYLES): "basic" is static text; the others are animated word by word
(karaoke): the whole line stays on screen and the word being spoken is coloured
and/or enlarged. Each word slot is its own Dialogue event with inline override
tags, which keeps Hebrew right-to-left layout correct (libass + fribidi reorder
the runs; ASS \\k karaoke would sweep left-to-right).
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    words: list[tuple[float, float, str]] = field(default_factory=list)  # relative times


@dataclass(frozen=True)
class Style:
    """ASS colours are &HAABBGGRR. `active_*` apply to the word being spoken."""
    font_size: int = FONT_SIZE
    bold: bool = True
    primary: str = "&H00FFFFFF"
    outline: str = "&H00000000"
    back: str = "&H80000000"
    border_style: int = 1  # 1 = outline + shadow, 3 = opaque box
    outline_w: float = 6
    shadow: float = 2
    margin_v: int = MARGIN_V
    karaoke: bool = True
    active_colour: str | None = None
    active_scale: int = 100
    uppercase: bool = False


STYLES: dict[str, Style] = {
    "basic": Style(karaoke=False),
    # white with a thick outline, spoken word in yellow and slightly bigger
    "bold": Style(active_colour="&H0000E5FF", active_scale=112),
    # white on a dark translucent box, spoken word in green
    "box": Style(font_size=72, border_style=4, outline_w=14, shadow=0, back="&H60000000",
                 outline="&H60000000", active_colour="&H0060E070"),
    # uppercase, spoken word pops (bigger) in pink, no colour box
    "pop": Style(font_size=80, outline_w=5, shadow=0, uppercase=True, active_colour="&H00B469FF",
                 active_scale=125),
}
DEFAULT_STYLE = "bold"


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
                    " ".join(x["word"] for x in g),
                    [(max(x["start"] - offset, 0.0), max(x["end"] - offset, 0.0), x["word"]) for x in g])
            for g in groups]
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


def _style_line(style: Style) -> str:
    # Encoding -1 makes libass pick the paragraph direction from the text. With the
    # default (1) the base direction is LTR, and once override tags split a Hebrew line
    # into several runs, the runs come out in left-to-right order.
    return (f"Style: Default,{FONT},{style.font_size},{style.primary},{style.primary},{style.outline},"
            f"{style.back},{-1 if style.bold else 0},0,0,0,100,100,0,0,{style.border_style},"
            f"{style.outline_w:g},{style.shadow:g},2,80,80,{style.margin_v},-1")


def _karaoke_events(c: Caption, style: Style) -> list[tuple[float, float, str]]:
    """One (start, end, text) per spoken word; the active word carries override tags."""
    words = c.words or [(c.start, c.end, w) for w in c.text.split()]
    shown = [w.upper() if style.uppercase else w for _, _, w in words]
    on = ""
    if style.active_colour:
        on += f"\\c{style.active_colour}"
    if style.active_scale != 100:
        on += f"\\fscx{style.active_scale}\\fscy{style.active_scale}"
    events = []
    for i, (ws, _, _) in enumerate(words):
        a = c.start if i == 0 else max(ws, events[-1][0])
        b = c.end if i == len(words) - 1 else max(words[i + 1][0], a)
        parts = [("{" + on + "}" + _escape(w) + "{\\r}") if j == i else _escape(w) for j, w in enumerate(shown)]
        if events:
            events[-1] = (events[-1][0], a, events[-1][2])
        events.append((a, b, " ".join(parts)))
    return [e for e in events if e[1] > e[0]]


def write_ass(captions: list[Caption], path: Path, width: int = 1080, height: int = 1920,
              style: str | Style = "basic") -> Path:
    st = STYLES[style] if isinstance(style, str) else style
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{_style_line(st)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in captions:
        if st.karaoke:
            events = _karaoke_events(c, st)
        else:
            events = [(c.start, c.end, _escape(c.text.upper() if st.uppercase else c.text))]
        lines += [f"Dialogue: 0,{_ts(a)},{_ts(b)},Default,,0,0,0,,{t}" for a, b, t in events]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path
