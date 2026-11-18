from clipmaker.segment import Sentence, candidate_clips, select_clips, split_sentences


def _seg(start, end, words):
    step = (end - start) / len(words)
    return {"start": start, "end": end, "text": " ".join(words),
            "words": [{"start": start + i * step, "end": start + (i + 1) * step, "word": w} for i, w in enumerate(words)]}


def test_split_on_punctuation_and_pause():
    segs = [
        _seg(0, 2, ["Hello", "there."]),
        _seg(2.1, 4, ["How", "are", "you?", "Fine"]),
        _seg(5.0, 6, ["thanks"]),  # 1 s pause before -> new sentence even without punctuation
    ]
    sents = split_sentences(segs)
    assert [s.text for s in sents] == ["Hello there.", "How are you?", "Fine", "thanks"]
    assert sents[1].start == 2.1 and sents[3].end == 6


def test_split_hebrew_punctuation():
    sents = split_sentences([_seg(0, 3, ["שלום", "לכולם.", "מה", "נשמע?"])])
    assert [s.text for s in sents] == ["שלום לכולם.", "מה נשמע?"]


def _sentences(n, dur=8.0, gap=0.5):
    return [Sentence(i * (dur + gap), i * (dur + gap) + dur, f"s{i}.") for i in range(n)]


def test_candidates_respect_length_limits():
    cands = candidate_clips(_sentences(12))
    assert cands
    assert all(15 <= c.duration <= 60 for c in cands)


def test_short_talk_gives_single_full_candidate():
    cands = candidate_clips(_sentences(1, dur=10))
    assert len(cands) == 1 and cands[0].start == 0 and cands[0].end == 10


def test_select_clips_no_overlap_and_ordered():
    chosen = select_clips(candidate_clips(_sentences(12)), max_clips=3)
    assert 1 <= len(chosen) <= 3
    for a, b in zip(chosen, chosen[1:]):
        assert a.last < b.first and a.start < b.start


def test_candidates_prefer_edges_in_pauses():
    # s1 and s2 touch (no pause): a clip may not start at s2 or end at s1
    sents = [Sentence(0, 8, "a."), Sentence(8.5, 16, "b."), Sentence(16, 24, "c."), Sentence(24.5, 32, "d.")]
    cands = candidate_clips(sents)
    assert cands
    assert all(c.first != 2 and c.last != 1 for c in cands)
