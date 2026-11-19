from clipmaker.captions import clip_words, group_words, write_ass
from clipmaker.media import probe
from clipmaker.render import center_crop, render_clip


def _words(text, start=10.0, step=0.4):
    return [{"start": start + i * step, "end": start + (i + 1) * step - 0.05, "word": w}
            for i, w in enumerate(text.split())]


def test_group_words_breaks_on_sentence_and_length():
    caps = group_words(_words("Stop scrolling. This one trick will save you an hour"), offset=10.0)
    texts = [c.text for c in caps]
    assert texts[0] == "Stop scrolling."
    assert all(len(t.split()) <= 4 and len(t) <= 22 for t in texts)
    assert caps[0].start == 0.0
    # gaps closed, in order
    assert all(a.end <= b.start + 1e-9 for a, b in zip(caps, caps[1:]))
    assert all(a.end == b.start for a, b in zip(caps, caps[1:]))


def test_clip_words_filters_range():
    ws = _words("a b c d e")
    assert [w["word"] for w in clip_words(ws, 10.4, 11.2)] == ["b", "c"]


def test_write_ass_escapes_and_times(tmp_path):
    caps = group_words(_words("hello {world}"), offset=10.0)
    text = write_ass(caps, tmp_path / "x.ass").read_text(encoding="utf-8")
    assert "PlayResY: 1920" in text
    assert "\\{world\\}" in text
    assert "Dialogue: 0,0:00:00.00," in text


def test_render_with_hebrew_captions(tiny_video, tmp_path):
    caps = group_words(_words("שלום לכולם, היום נדבר.", start=0.2), offset=0.0)
    ass = write_ass(caps, tmp_path / "he.ass")
    out = render_clip(tiny_video, tmp_path / "c.mp4", 0.0, 2.0, center_crop(640, 360), subtitles=ass)
    assert probe(out).height == 1920


def _highlight_side(tmp_path, text, style="bold"):
    """Render a caption line whose first word is active; return the active word's
    horizontal centre relative to the whole line (0 = left edge, 1 = right edge)."""
    import shutil
    import subprocess

    import numpy as np
    import pytest

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    ws = [{"start": 0.0 + i * 0.5, "end": 0.45 + i * 0.5, "word": w} for i, w in enumerate(text.split())]
    ass = write_ass(group_words(ws, 0.0, max_words=8, max_chars=60), tmp_path / "hl.ass", style=style)
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=1",
         "-vf", f"subtitles=filename={ass.as_posix()}", "-ss", "0.2", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    img = np.frombuffer(proc.stdout, np.uint8).reshape(1920, 1080, 3).astype(int)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    text_cols = np.nonzero(((r > 200) & (g > 180)).any(axis=0))[0]
    hl_cols = np.nonzero(((r > 200) & (g > 180) & (b < 90)).any(axis=0))[0]
    assert len(hl_cols) and len(text_cols)
    return (hl_cols.mean() - text_cols.min()) / (text_cols.max() - text_cols.min())


def test_karaoke_first_word_is_left_in_english(tmp_path):
    assert _highlight_side(tmp_path, "one two three four") < 0.3


def test_karaoke_first_word_is_right_in_hebrew(tmp_path):
    # RTL: the first spoken word must be drawn at the right end of the line
    assert _highlight_side(tmp_path, "אחת שתיים שלוש ארבע") > 0.7


def test_styles_write_karaoke_events(tmp_path):
    from clipmaker.captions import STYLES

    caps = group_words(_words("one two three"), offset=10.0)
    for name in STYLES:
        text = write_ass(caps, tmp_path / f"{name}.ass", style=name).read_text(encoding="utf-8")
        events = [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]
        assert len(events) == (1 if name == "basic" else 3)
        assert text.count(",-1\n") == 1  # auto base direction for RTL
    assert "ONE" in (tmp_path / "pop.ass").read_text(encoding="utf-8")
