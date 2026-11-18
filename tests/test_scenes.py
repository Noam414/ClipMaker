import subprocess

from clipmaker.scenes import detect_scene_cuts, parse_scdet


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
