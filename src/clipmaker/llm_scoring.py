"""Optional LLM re-ranking of candidate clips. Off by default.

Enabled only when CLIPMAKER_LLM_SCORING=1 and ANTHROPIC_API_KEY is set (see
NEEDS_HUMAN H-004). The top-K heuristic candidates go to the model in one
request; its 0..1 virality scores are blended with the heuristic score. Any
failure (network, bad JSON) leaves the heuristic scores unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from typing import Callable, Protocol

from clipmaker.segment import Candidate

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5"
TOP_K = 12
BLEND = 0.5  # weight of the LLM score in the final score

PROMPT = """You rate excerpts from a long video as standalone short-form clips (TikTok/Reels/Shorts).
For each numbered excerpt, give a score from 0 to 1 for how well it works on its own:
a strong opening hook in the first sentence, a complete thought, and a payoff.
Reply with only a JSON array of numbers, one per excerpt, in order.

{excerpts}"""


class Scorer(Protocol):
    def score(self, texts: list[str]) -> list[float]: ...


Transport = Callable[[str, dict, dict], dict]


def _http_post(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


class AnthropicScorer:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, transport: Transport = _http_post):
        self.api_key, self.model, self.transport = api_key, model, transport

    def score(self, texts: list[str]) -> list[float]:
        excerpts = "\n\n".join(f"[{i + 1}] {t}" for i, t in enumerate(texts))
        body = {"model": self.model, "max_tokens": 256,
                "messages": [{"role": "user", "content": PROMPT.format(excerpts=excerpts)}]}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        data = self.transport(API_URL, headers, body)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return parse_scores(text, len(texts))


def parse_scores(text: str, n: int) -> list[float]:
    match = re.search(r"\[[^\[\]]*\]", text)
    if not match:
        raise ValueError("no JSON array in reply")
    values = json.loads(match.group(0))
    if len(values) != n:
        raise ValueError(f"expected {n} scores, got {len(values)}")
    return [min(max(float(v), 0.0), 1.0) for v in values]


def scorer_from_env() -> Scorer | None:
    if os.environ.get("CLIPMAKER_LLM_SCORING") != "1":
        return None
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        log.warning("CLIPMAKER_LLM_SCORING=1 but ANTHROPIC_API_KEY is not set; using heuristics only")
        return None
    return AnthropicScorer(key, os.environ.get("CLIPMAKER_LLM_MODEL", DEFAULT_MODEL))


def blend_scores(candidates: list[Candidate], scorer: Scorer | None, top_k: int = TOP_K) -> dict[int, float]:
    """Re-rank the top_k candidates in place. Returns id(candidate) -> llm score."""
    if scorer is None or not candidates:
        return {}
    top = sorted(candidates, key=lambda c: -c.score)[:top_k]
    try:
        llm = scorer.score([c.text for c in top])
    except Exception as exc:  # never fail the pipeline because of the optional scorer
        log.warning("LLM scoring failed, keeping heuristic scores: %s", exc)
        return {}
    out = {}
    for c, s in zip(top, llm):
        c.score = round((1 - BLEND) * c.score + BLEND * s, 4)
        out[id(c)] = s
    return out
