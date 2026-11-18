"""Dead-air and filler removal inside a clip.

Works on VAD chunk edges (the reliable timings we have, see D-002):
- pauses between chunks longer than MAX_PAUSE are shortened to about KEEP_PAUSE
- chunks that contain only filler words ("um", "uh", "אה") are dropped
Filler words inside a sentence are left alone until we have real word
timestamps; cutting on estimated word times would clip real words.
"""
from __future__ import annotations

import re

MAX_PAUSE = 1.0
KEEP_PAUSE = 0.4  # silence kept at a shortened pause (split around the cut)
MIN_RANGE = 0.05
FILLERS = {"um", "uh", "umm", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm", "אה", "אמ", "אממ", "אהה", "אמממ"}


def is_filler_only(text: str) -> bool:
    words = re.findall(r"[\w']+", text.lower())
    return bool(words) and all(w in FILLERS for w in words)


def kept_ranges(segments: list[dict], start: float, end: float,
                max_pause: float = MAX_PAUSE, keep_pause: float = KEEP_PAUSE) -> list[list[float]]:
    """Source-time ranges to keep for a clip [start, end]."""
    speech = [(max(s["start"], start), min(s["end"], end)) for s in segments
              if s["end"] > start and s["start"] < end and not is_filler_only(s.get("text", ""))]
    if not speech:
        return [[start, end]]
    pad = keep_pause / 2
    ranges: list[list[float]] = []
    cur_start = start
    prev_end = speech[0][1]
    # leading silence before the first speech chunk
    if speech[0][0] - start > max_pause:
        cur_start = speech[0][0] - pad
    for s0, s1 in speech[1:]:
        if s0 - prev_end > max_pause:
            ranges.append([cur_start, prev_end + pad])
            cur_start = s0 - pad
        prev_end = max(prev_end, s1)
    tail_end = end if end - prev_end <= max_pause else prev_end + pad
    ranges.append([cur_start, tail_end])
    return [[round(a, 3), round(b, 3)] for a, b in ranges if b - a >= MIN_RANGE]


def output_duration(ranges: list[list[float]]) -> float:
    return sum(b - a for a, b in ranges)


def map_time(ranges: list[list[float]], t: float) -> float | None:
    """Source time -> clip output time, or None if t falls in a removed part."""
    acc = 0.0
    for a, b in ranges:
        if a <= t <= b:
            return acc + (t - a)
        acc += b - a
    return None


def remap_words(words: list[dict], ranges: list[list[float]]) -> list[dict]:
    """Words with times on the clip's output timeline; words in removed parts are dropped."""
    out = []
    for w in words:
        s, e = map_time(ranges, w["start"]), map_time(ranges, w["end"])
        if s is None and e is None:
            continue
        if s is None:
            s = max(e - (w["end"] - w["start"]), 0.0)
        if e is None:
            e = s + (w["end"] - w["start"])
        out.append({**w, "start": round(s, 3), "end": round(e, 3)})
    return out
