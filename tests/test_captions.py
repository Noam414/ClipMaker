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
