import subprocess

from clipmaker.scenes import detect_scene_cuts, detect_scene_cuts_in, padded_ranges, parse_scdet


def test_parse_scdet_merges_close_hits():
    log = "x lavfi.scd.score: 40, lavfi.scd.time: 6\n lavfi.scd.time: 6.2\n lavfi.scd.time: 12.04\n"
    assert parse_scdet(log) == [6.0, 12.04]


def test_detects_cut_between_colors(tmp_path):
    out = tmp_path / "cut.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:size=320x180:rate=25:duration=2",
         "-f", "lavfi", "-i", "color=c=blue:size=320x180:rate=25:duration=2",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )
    cuts = detect_scene_cuts(out)
    assert len(cuts) == 1 and abs(cuts[0] - 2.0) < 0.1


def test_no_cuts_in_static_video(tiny_video):
    assert detect_scene_cuts(tiny_video) == []


def _four_colours(path):
    graph = "".join(f"[{i}:v]" for i in range(4)) + "concat=n=4:v=1[v]"
    inputs = []
    for c in ("red", "blue", "green", "white"):
        inputs += ["-f", "lavfi", "-i", f"color=c={c}:size=320x180:rate=25:duration=3"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "libx264", "-g", "250", "-pix_fmt", "yuv420p", str(path)], check=True)


def test_padded_ranges_clamps_and_merges():
    assert padded_ranges([(10.0, 20.0), (1.0, 3.0), (21.0, 25.0)], duration=26.0, padding=2.0) == \
        [(0.0, 5.0), (8.0, 26.0)]
    assert padded_ranges([]) == []


def test_range_detection_reports_source_times_and_skips_the_rest(tmp_path):
    video = tmp_path / "four.mp4"  # cuts at 3, 6, 9 s; one keyframe, so seeking must decode
    _four_colours(video)
    assert [round(c) for c in detect_scene_cuts(video, start=4.5, end=7.5)] == [6]
    # spans padded by 2 s: (0.0..4.5) and (6.5..11.0) -> cuts at 3 and 9, not 6
    cuts = detect_scene_cuts_in(video, [(2.0, 2.5), (8.5, 9.0)], duration=12.0)
    assert [round(c) for c in cuts] == [3, 9]
