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
    assert m.wer("every five minutes", "every 5 minutes") == 0


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


def test_face_inside_rate_accounts_for_punch_in_zoom():
    # Face near the left edge of a 608x1080 crop: visible normally, cut off at 1.12x zoom.
    crop = [[0, 600, 0, 608, 1080]]
    track = [[0.5, 600 + 115.0, 420.0], [2.5, 600 + 115.0, 420.0]]
    clip = {"start": 0, "end": 3, "crop_track": crop, "punch_ins": [[2.0, 3.0]], "punch_in_scale": 1.12}
    assert m.face_inside_rate([clip], track, [220, 260]) == 0.5
    no_zoom = dict(clip, punch_ins=[])
    assert m.face_inside_rate([no_zoom], track, [220, 260]) == 1.0


def test_face_inside_rate_maps_punch_ins_through_kept_ranges():
    crop = [[0, 600, 0, 608, 1080]]
    # source 5.5 maps to output 2.5 (ranges keep 0-1 and 4-6); 2.0 was cut out -> skipped
    track = [[2.0, 0.0, 420.0], [5.5, 600 + 115.0, 420.0]]
    clip = {"start": 0, "end": 6, "crop_track": crop, "kept_ranges": [[0, 1], [4, 6]],
            "punch_ins": [[2.2, 3.0]], "punch_in_scale": 1.12}
    assert m.face_inside_rate([clip], track, [220, 260]) == 0.0
    clip["punch_ins"] = [[0.0, 1.0]]
    assert m.face_inside_rate([clip], track, [220, 260]) == 1.0


def test_visible_crop_matches_zoom_anchor():
    x, y, w, h = m._visible_crop([100, 0, 608, 1080], 1.12)
    assert abs(w - 608 / 1.12) < 1e-6 and abs(h - 1080 / 1.12) < 1e-6
    assert abs((x - 100) - 0.5 * (608 - w)) < 1e-6
    assert abs(y - 0.35 * (1080 - h)) < 1e-6


def test_speed_report_extrapolates_per_input_stages_only():
    result = {"timings": {"scenes_seconds": 20.0, "transcribe_seconds": 60.0,
                          "render_seconds": 50.0, "total_seconds": 135.0}}
    rep = m.speed_report(result, elapsed=140.0, duration=600.0)
    assert rep["time_to_all_clips_s"] == 140.0
    assert rep["stages_s"] == {"scenes": 20.0, "transcribe": 60.0, "render": 50.0, "other": 10.0}
    # (140 - 50) * 6 + 50: input-proportional work scales to 60 min, rendering doesn't
    assert rep["extrapolated_60min_s"] == 590.0
    assert rep["meets_target"] is False
    assert m.speed_report(None, 1.0, 600.0) is None
    # scenes on the selected ranges only: per-clip work, not scaled -> (140 - 70) * 6 + 70
    ranged = m.speed_report({**result, "scene_cuts_scope": "clips"}, elapsed=140.0, duration=600.0)
    assert ranged["extrapolated_60min_s"] == 490.0


def test_speed_fixture_is_reported_but_kept_out_of_quality_averages(tmp_path):
    fast = {"metrics": {**m.score_fixture({"duration": 60}, None, tmp_path, 6.0)["metrics"], "wer": 0.1}}
    slow = m.score_fixture({"duration": 600, "speed_fixture": True},
                           {"timings": {"render_seconds": 10.0}, "clips": []}, tmp_path, 40.0)
    slow["metrics"]["wer"] = 0.9
    summary = m.aggregate({"a": fast, "long": slow})
    assert summary["wer"] == 0.1
    assert summary["seconds_per_input_minute"] == 6.0
    assert summary["time_to_all_clips_s"] == 40.0
    assert summary["extrapolated_60min_s"] == 190.0  # (40 - 10) * 6 + 10
