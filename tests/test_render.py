from clipmaker.media import probe
from clipmaker.render import Crop, build_filter, center_crop, render_clip


def test_center_crop_landscape():
    c = center_crop(1920, 1080)
    assert c.h == 1080 and abs(c.w / c.h - 9 / 16) < 0.01
    assert c.x == (1920 - c.w) // 2 and c.y == 0


def test_center_crop_vertical_untouched():
    assert center_crop(1080, 1920) == Crop(0, 0, 1080, 1920)
    assert center_crop(720, 1280) == Crop(0, 0, 720, 1280)


def test_center_crop_too_tall():
    c = center_crop(1080, 2400)
    assert c.w == 1080 and abs(c.w / c.h - 9 / 16) < 0.01 and c.y > 0


def test_filter_escapes_windows_path():
    f = build_filter(Crop(0, 0, 10, 20), subtitles=__import__("pathlib").Path("C:/x/subs.ass"))
    assert "C\\:/x/subs.ass" in f


def test_render_clip_is_accurate_and_vertical(tiny_video, tmp_path):
    out = render_clip(tiny_video, tmp_path / "c.mp4", 0.5, 2.5, center_crop(640, 360))
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert info.has_audio
    assert abs(info.duration - 2.0) < 0.1


def _frame(path, t):
    import subprocess

    import numpy as np

    proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                           "-vf", "scale=270:480", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                          capture_output=True, check=True)
    return np.frombuffer(proc.stdout, np.uint8).reshape(480, 270).astype(int)


def test_zoom_punch_in_only_inside_window(tiny_video, tmp_path):
    from clipmaker.keywords import zoom_expression

    crop = center_crop(640, 360)
    plain = render_clip(tiny_video, tmp_path / "p.mp4", 0.0, 3.0, crop, fps=25)
    zoomed = render_clip(tiny_video, tmp_path / "z.mp4", 0.0, 3.0, crop,
                         zoom=zoom_expression([(1.0, 2.0)], 1.3), fps=25)
    info = probe(zoomed)
    assert (info.width, info.height) == (1080, 1920) and abs(info.duration - 3.0) < 0.15
    diff = lambda t: abs(_frame(plain, t) - _frame(zoomed, t)).mean()
    assert diff(0.5) < 3 and diff(2.5) < 3
    assert diff(1.5) > 10


def test_zoom_with_dead_air_ranges(tiny_video, tmp_path):
    from clipmaker.keywords import zoom_expression

    out = render_clip(tiny_video, tmp_path / "r.mp4", 0.0, 3.0, center_crop(640, 360),
                      ranges=[[0.0, 1.2], [1.6, 3.0]], zoom=zoom_expression([(0.5, 1.0)]), fps=25)
    assert abs(probe(out).duration - 2.6) < 0.15
