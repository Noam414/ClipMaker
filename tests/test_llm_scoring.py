import pytest

from clipmaker.llm_scoring import AnthropicScorer, blend_scores, parse_scores, scorer_from_env
from clipmaker.segment import Candidate


def _cands():
    return [Candidate(0, 20, 0, 1, "Here is the thing.", 0.8), Candidate(30, 50, 2, 3, "Welcome back.", 0.7)]


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("CLIPMAKER_LLM_SCORING", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert scorer_from_env() is None


def test_needs_key(monkeypatch):
    monkeypatch.setenv("CLIPMAKER_LLM_SCORING", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert scorer_from_env() is None


def test_parse_scores():
    assert parse_scores("Sure: [0.9, 1.4, -1]", 3) == [0.9, 1.0, 0.0]
    with pytest.raises(ValueError):
        parse_scores("[0.5]", 2)


def test_anthropic_scorer_with_fake_transport():
    seen = {}

    def fake(url, headers, body):
        seen.update(url=url, headers=headers, body=body)
        return {"content": [{"type": "text", "text": "[0.2, 1.0]"}]}

    cands = _cands()
    llm = blend_scores(cands, AnthropicScorer("k", transport=fake))
    assert seen["headers"]["x-api-key"] == "k"
    assert "[1] Here is the thing." in seen["body"]["messages"][0]["content"]
    assert cands[0].score == pytest.approx(0.5) and cands[1].score == pytest.approx(0.85)
    assert len(llm) == 2


def test_failure_keeps_heuristic_scores():
    def broken(url, headers, body):
        raise OSError("network down")

    cands = _cands()
    assert blend_scores(cands, AnthropicScorer("k", transport=broken)) == {}
    assert [c.score for c in cands] == [0.8, 0.7]
