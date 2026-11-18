import pytest

from clipmaker.media import probe
from clipmaker.render import center_crop, render_clip
from clipmaker.trim import is_filler_only, kept_ranges, map_time, output_duration, remap_words

SEGS = [
    {"start": 10.0, "end": 14.0, "text": "First sentence."},
    {"start": 14.3, "end": 17.0, "text": "Second one."},
    {"start": 20.0, "end": 20.4, "text": "Um."},
    {"start": 21.0, "end": 25.0, "text": "After the pause."},
]


def test_filler_only():
    assert is_filler_only("Um.") and is_filler_only("uh, um") and is_filler_only("אה")
    assert not is_filler_only("Um, so here is the thing")


def test_long_pause_and_filler_chunk_removed():
    r = kept_ranges(SEGS, 10.0, 25.0)
    assert len(r) == 2
    assert r[0][0] == 10.0 and r[0][1] == pytest.approx(17.2)
    assert r[1][0] == pytest.approx(20.8) and r[1][1] == 25.0
    # short pause (0.3 s) kept as is
    assert map_time(r, 14.15) == pytest.approx(4.15)
    assert map_time(r, 18.5) is None
    assert output_duration(r) == pytest.approx(25 - 10 - (20.8 - 17.2))


def test_no_speech_keeps_whole_range():
    assert kept_ranges([], 0, 5) == [[0, 5]]


def test_remap_words():
    r = [[10.0, 12.0], [15.0, 17.0]]
    words = [{"start": 10.5, "end": 11.0, "word": "a"}, {"start": 13, "end": 14, "word": "gone"},
             {"start": 15.5, "end": 16.0, "word": "b"}]
    out = remap_words(words, r)
    assert [w["word"] for w in out] == ["a", "b"]
    assert out[1]["start"] == pytest.approx(2.5)


def test_render_multi_range(tiny_video, tmp_path):
    out = render_clip(tiny_video, tmp_path / "m.mp4", 0.0, 3.0, center_crop(640, 360),
                      ranges=[[0.0, 1.0], [2.0, 3.0]])
    info = probe(out)
    assert abs(info.duration - 2.0) < 0.15 and info.height == 1920 and info.has_audio
