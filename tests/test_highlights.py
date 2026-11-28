from clipmaker.highlights import content_score, hook_score, rescore, score_candidate, starts_with_filler
from clipmaker.segment import Candidate, Sentence, select_clips


def test_hook_beats_filler():
    assert hook_score("Here is the thing nobody tells you about motivation.") > 0.6
    assert hook_score("Um, so, before we start, let me check the microphone levels.") == 0.0
    assert hook_score("Why do most people quit?") > hook_score("We went to the store.")
    assert hook_score("הסוד הוא לא ללמוד יותר שעות.") >= 0.6


def test_filler_detection():
    assert starts_with_filler("Anyway, that is it for today.")
    assert starts_with_filler("Sorry, I lost my notes.")
    assert not starts_with_filler("Stop scrolling.")


def test_content_score_rewards_signals():
    assert content_score("Six months later the results surprised me. Why?") > content_score("We walked home and had dinner.")


SENTS = [
    Sentence(0, 5, "Welcome back to the show."),
    Sentence(5.5, 10, "Um, so, let me check the levels."),
    Sentence(10.5, 15, "Here is the thing nobody tells you."),
    Sentence(15.5, 22, "Motivation is the result of action, not the cause."),
    Sentence(22.5, 30, "You start, and the feeling follows."),
    Sentence(30.5, 40, "I quit the gym four times in one year."),
    Sentence(40.5, 48, "Anyway, thanks for listening."),
]


def _cand(i, j):
    return Candidate(SENTS[i].start, SENTS[j].end, i, j, " ".join(s.text for s in SENTS[i:j + 1]), 0.0)


def test_candidate_starting_on_hook_wins():
    hook = score_candidate(_cand(2, 5), SENTS).total
    filler = score_candidate(_cand(1, 4), SENTS).total
    intro = score_candidate(_cand(0, 3), SENTS).total
    assert hook > filler and hook > intro


def test_rescore_then_select_picks_hook():
    cands = [_cand(0, 3), _cand(1, 4), _cand(2, 5), _cand(3, 6)]
    rescore(cands, SENTS)
    best = select_clips(cands, max_clips=1)[0]
    assert best.first == 2


def test_outro_ending_is_penalized():
    assert score_candidate(_cand(2, 5), SENTS).total > score_candidate(_cand(2, 6), SENTS).total
