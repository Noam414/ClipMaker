import pytest

from clipmaker import metrics as m

SEGS = [
    {"start": 0.0, "end": 4.0, "text": "Hello there.", "kind": "filler"},
    {"start": 4.5, "end": 9.0, "text": "Here is the thing.", "kind": "hook"},
    {"start": 9.4, "end": 20.0, "text": "It works.", "kind": "story"},
]


def test_wer_basic():
    assert m.wer("hello world", "hello world") == 0
    assert m.wer("Hello, world!", "hello world") == 0
    assert m.wer("a b c d", "a x c") == pytest.approx(0.5)
    assert m.wer("שלום לכולם", "שלום לכולם.") == 0


def test_boundary_and_hook():
    clips = [{"start": 4.6, "end": 19.8}, {"start": 1.0, "end": 9.0}]
    assert m.boundary_accuracy(clips, SEGS) == 0.5
    assert m.hook_start_rate(clips, SEGS) == 0.5
    assert m.boundary_accuracy([], SEGS) is None


def test_dead_air_remaining_uses_kept_ranges():
    dead = [{"start": 10.0, "end": 13.0}]
    assert m.dead_air_remaining([{"start": 9, "end": 20}], dead) == 3.0
    assert m.dead_air_remaining([{"start": 9, "end": 20, "kept_ranges": [[9, 10.2], [12.9, 20]]}], dead) == pytest.approx(0.3)


def test_scene_cut_pr():
    p, r = m.scene_cut_pr([6.1, 12.0, 15.0], [6, 12, 18, 24])
    assert p == pytest.approx(2 / 3) and r == 0.5
    assert m.scene_cut_pr(None, [6]) == (None, None)


def test_face_inside_rate():
    track = [[0.0, 960.0, 420.0], [1.0, 1500.0, 420.0]]
    clip = {"start": 0, "end": 1, "crop_track": [[0, 656, 0, 608, 1080]]}
    assert m.face_inside_rate([clip], track, [220, 260]) == 0.5
    assert m.face_inside_rate([{"start": 0, "end": 1}], track, [220, 260]) is None


def test_score_fixture_without_result(tmp_path):
    rep = m.score_fixture({"duration": 60}, None, tmp_path, 3.0)
    assert rep["metrics"]["seconds_per_input_minute"] == 3.0
    assert m.aggregate({"a": rep})["fixtures"] == 1
