"""Heuristic highlight scoring (no ML): how likely a candidate clip is to work as a short.

Signals (all in 0..1, combined with fixed weights):
- hook:         the opening sentence grabs attention (hook phrases, a question,
                a number, direct address), and doesn't open with filler
- content:      density of questions, numbers and strong/emotional words in the clip
- completeness: the clip ends on a finished sentence
- pacing:       speech rate in a comfortable 2-3.8 words/s range
- length:       closeness to ~30 s
English and Hebrew word lists. An LLM scorer can replace or blend in later (M2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from clipmaker.segment import SENTENCE_END, Candidate, Sentence

WEIGHTS = {"hook": 0.4, "content": 0.2, "completeness": 0.1, "pacing": 0.1, "length": 0.2}
FILLER_START_PENALTY = 0.35
FILLER_END_PENALTY = 0.15

HOOK_PHRASES = [
    # English
    "here is the thing", "here's the thing", "nobody tells you", "no one tells you", "the secret",
    "the truth", "the problem", "the mistake", "the rule", "simple rule", "one trick", "this one",
    "stop ", "you need to", "you have to", "you do not", "you don't", "most people", "the reason",
    "what if", "why ", "how to", "the key", "the biggest", "never ", "always ", "is not", "isn't",
    # Hebrew
    "הסוד", "האמת", "הטריק", "הכלל", "אף אחד לא", "רוב האנשים", "למה ", "איך ", "תפסיקו", "אתם חייבים",
    "הבעיה", "הטעות", "נסו",
]
FILLER_STARTS = [
    "um", "uh", "so,", "anyway", "sorry", "welcome back", "thanks for", "before we start",
    "let me check", "okay so", "alright", "as i said",
    "אה", "אממ", "בקיצור", "טוב,", "רגע", "סליחה", "ברוכים הבאים", "תודה",
]
STRONG_WORDS = [
    "surprised", "shocked", "amazing", "incredible", "hard way", "mistake", "secret", "never", "always",
    "love", "hate", "fail", "failed", "quit", "quitting", "every time", "results", "excuse", "truth",
    "מדהים", "טעות", "סוד", "אף פעם", "תמיד", "נכשלתי", "תוצאות", "ההבדל",
]
NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                "hundred", "thousand", "million", "twice", "half",
                "אחת", "שתיים", "שלוש", "ארבע", "חמש", "עשר", "עשרים", "מאה", "אלף"}
SECOND_PERSON = {"you", "your", "you're", "yourself", "אתם", "אתה", "את", "שלכם", "לכם"}


@dataclass
class Score:
    total: float
    parts: dict = field(default_factory=dict)


def _norm(text: str) -> str:
    return " " + re.sub(r"\s+", " ", re.sub(r"[^\w\s',?!.]", " ", text.lower())).strip() + " "


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def starts_with_filler(text: str) -> bool:
    t = _norm(text).strip()
    return any(t.startswith(f) for f in FILLER_STARTS)


def hook_score(text: str) -> float:
    t = _norm(text)
    toks = _tokens(text)
    s = 0.0
    if any(p in t for p in HOOK_PHRASES):
        s += 0.6
    if "?" in text:
        s += 0.3
    if any(w.isdigit() or w in NUMBER_WORDS for w in toks):
        s += 0.2
    if any(w in SECOND_PERSON for w in toks):
        s += 0.2
    if starts_with_filler(text):
        s -= 0.6
    return max(0.0, min(s, 1.0))


def content_score(text: str) -> float:
    toks = _tokens(text)
    if not toks:
        return 0.0
    t = _norm(text)
    hits = text.count("?") + sum(w.isdigit() or w in NUMBER_WORDS for w in toks)
    hits += sum(t.count(" " + w) for w in STRONG_WORDS)
    hits += sum(t.count(p) for p in HOOK_PHRASES)
    return min(hits / (len(toks) / 12), 1.0)  # ~1 signal per 12 words saturates


def pacing_score(n_words: int, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    rate = n_words / seconds
    if 2.0 <= rate <= 3.8:
        return 1.0
    return max(0.0, 1.0 - min(abs(rate - 2.0), abs(rate - 3.8)) / 1.5)


def length_score(seconds: float, ideal: float = 30.0) -> float:
    return 1.0 - min(abs(seconds - ideal) / ideal, 1.0)


def score_candidate(c: Candidate, sentences: list[Sentence]) -> Score:
    sents = sentences[c.first:c.last + 1]
    # the opening = first sentence, or the first two if the first is very short ("Okay.")
    opening = sents[0].text
    if len(_tokens(opening)) <= 2 and len(sents) > 1:
        opening = opening + " " + sents[1].text
    text = " ".join(s.text for s in sents)
    parts = {
        "hook": hook_score(opening),
        "content": content_score(text),
        "completeness": 1.0 if sents[-1].text.rstrip("\"'”’)").endswith(SENTENCE_END) else 0.4,
        "pacing": pacing_score(len(_tokens(text)), c.duration),
        "length": length_score(c.duration),
    }
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    if starts_with_filler(sents[0].text):
        total -= FILLER_START_PENALTY
    if len(sents) > 1 and starts_with_filler(sents[-1].text):
        total -= FILLER_END_PENALTY  # trailing outro / aside ("Anyway, thanks for listening")
    return Score(round(max(total, 0.0), 4), {k: round(v, 3) for k, v in parts.items()})


def rescore(candidates: list[Candidate], sentences: list[Sentence]) -> dict[int, dict]:
    """Replace each candidate's score in place; returns id(candidate) -> parts for reporting."""
    parts = {}
    for c in candidates:
        s = score_candidate(c, sentences)
        c.score = s.total
        parts[id(c)] = s.parts
    return parts
