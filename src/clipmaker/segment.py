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


def select_clips(candidates: list[Candidate], max_clips: int = 5) -> list[Candidate]:
    """Greedy: best score first, no overlapping sentences. Returned in time order."""
    chosen: list[Candidate] = []
    for c in sorted(candidates, key=lambda c: (-c.score, c.start)):
        if len(chosen) >= max_clips:
            break
        if all(c.last < o.first or c.first > o.last for o in chosen):
            chosen.append(c)
    return sorted(chosen, key=lambda c: c.start)
