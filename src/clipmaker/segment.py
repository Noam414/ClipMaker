"""Sentence segmentation and candidate clip selection."""
from __future__ import annotations

from dataclasses import dataclass

SENTENCE_END = (".", "?", "!", "…", "。", "؟")
MIN_CLIP_S = 15.0
MAX_CLIP_S = 60.0
# A pause this long between VAD chunks also ends a sentence, even without punctuation
# (Whisper sometimes drops the final period of a chunk).
PAUSE_SPLIT_S = 0.6
# Clip edges should sit in a real pause: word times inside a VAD chunk are
# estimates, so cutting there risks clipping a word.
CLEAN_CUT_GAP_S = 0.3


@dataclass
class Sentence:
    start: float
    end: float
    text: str


def split_sentences(segments: list[dict]) -> list[Sentence]:
    """Turn transcript segments (with words) into sentences."""
    sentences: list[Sentence] = []
    cur: list[dict] = []

    def flush():
        if cur:
            sentences.append(Sentence(cur[0]["start"], cur[-1]["end"], " ".join(w["word"] for w in cur)))
            cur.clear()

    prev_end = None
    for seg in segments:
        words = seg.get("words") or []
        if cur and prev_end is not None and words and words[0]["start"] - prev_end >= PAUSE_SPLIT_S:
            flush()
        for w in words:
            cur.append(w)
            if w["word"].rstrip("\"'”’)").endswith(SENTENCE_END):
                flush()
        if words:
            prev_end = words[-1]["end"]
    flush()
    return sentences


@dataclass
class Candidate:
    start: float
    end: float
    first: int  # index of first sentence
    last: int  # index of last sentence (inclusive)
    text: str
    score: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _base_score(dur: float, n_sentences: int) -> float:
    """Placeholder until M2: prefer ~30 s clips with several complete sentences."""
    length = 1.0 - min(abs(dur - 30.0) / 30.0, 1.0)
    return round(0.7 * length + 0.3 * min(n_sentences / 4, 1.0), 4)


def _clean_edges(sentences: list[Sentence], gap: float) -> tuple[list[bool], list[bool]]:
    n = len(sentences)
    can_start = [i == 0 or sentences[i].start - sentences[i - 1].end >= gap for i in range(n)]
    can_end = [i == n - 1 or sentences[i + 1].start - sentences[i].end >= gap for i in range(n)]
    return can_start, can_end


def candidate_clips(sentences: list[Sentence], min_s: float = MIN_CLIP_S, max_s: float = MAX_CLIP_S,
                    clean_gap: float = CLEAN_CUT_GAP_S) -> list[Candidate]:
    """Every run of whole sentences lasting min_s..max_s seconds.

    Runs whose edges sit in a pause of at least clean_gap seconds are preferred;
    if there are none, all runs are returned. If the whole talk is shorter than
    min_s, the full span is the only candidate.
    """
    clean = _candidates(sentences, min_s, max_s, clean_gap)
    return clean if clean else _candidates(sentences, min_s, max_s, 0.0)


def _candidates(sentences: list[Sentence], min_s: float, max_s: float, clean_gap: float) -> list[Candidate]:
    if not sentences:
        return []
    total = sentences[-1].end - sentences[0].start
    if total < min_s:
        return [Candidate(sentences[0].start, sentences[-1].end, 0, len(sentences) - 1,
                          " ".join(s.text for s in sentences), _base_score(total, len(sentences)))]
    can_start, can_end = _clean_edges(sentences, clean_gap)
    out = []
    for i, first in enumerate(sentences):
        if not can_start[i]:
            continue
        for j in range(i, len(sentences)):
            dur = sentences[j].end - first.start
            if dur > max_s:
                break
            if dur >= min_s and can_end[j]:
                out.append(Candidate(first.start, sentences[j].end, i, j,
                                     " ".join(s.text for s in sentences[i:j + 1]), _base_score(dur, j - i + 1)))
    return out


def select_clips(candidates: list[Candidate], max_clips: int = 5, min_score: float = 0.6) -> list[Candidate]:
    """Pick up to max_clips non-overlapping candidates maximizing the sum of (score - min_score).

    Weighted interval scheduling over sentence indices, so one long clip can't
    block two strong ones. Clips scoring below min_score are only used when
    nothing clears it (then the single best candidate is returned).
    Returned in time order.
    """
    if not candidates:
        return []
    good = sorted((c for c in candidates if c.score > min_score), key=lambda c: (c.last, c.first))
    if not good:
        return [max(candidates, key=lambda c: (c.score, -c.start))]
    from bisect import bisect_right

    lasts = [c.last for c in good]
    # prev[i]: number of candidates (in sorted order) that end before good[i] starts
    prev = [bisect_right(lasts, c.first - 1) for c in good]
    n = len(good)
    # best[k][i]: best value using the first i candidates and at most k clips
    best = [[0.0] * (n + 1) for _ in range(max_clips + 1)]
    for k in range(1, max_clips + 1):
        row, below = best[k], best[k - 1]
        for i in range(1, n + 1):
            c = good[i - 1]
            row[i] = max(row[i - 1], below[prev[i - 1]] + (c.score - min_score))
    chosen, k, i = [], max_clips, n
    while k > 0 and i > 0:
        c = good[i - 1]
        if best[k][i] == best[k][i - 1]:
            i -= 1
        else:
            chosen.append(c)
            i, k = prev[i - 1], k - 1
    return sorted(chosen, key=lambda c: c.start)
