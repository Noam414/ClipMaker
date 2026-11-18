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
