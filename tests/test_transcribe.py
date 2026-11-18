import os

import pytest

from clipmaker.metrics import wer
from clipmaker.transcribe import spread_words


def test_spread_words_covers_span_in_order():
    words = spread_words("Here is the thing", 10.0, 12.0)
    assert [w.word for w in words] == ["Here", "is", "the", "thing"]
    assert words[0].start == 10.0 and words[-1].end == 12.0
    assert all(a.end <= b.start + 1e-6 for a, b in zip(words, words[1:]))
    # longer words get longer spans
    assert (words[3].end - words[3].start) > (words[1].end - words[1].start)


def test_spread_words_empty():
    assert spread_words("   ", 0, 1) == []


def _models_available() -> bool:
    from clipmaker.config import get_settings

    folder = get_settings().cache_dir / "models" / "sherpa-onnx-whisper-base"
    return os.environ.get("CLIPMAKER_TEST_MODELS") == "1" or (folder / "base-encoder.int8.onnx").is_file()


@pytest.mark.skipif(not _models_available(), reason="whisper model not cached; set CLIPMAKER_TEST_MODELS=1 to download")
def test_transcribe_portrait_fixture(manifest):
    from clipmaker.transcribe import transcribe

    from conftest import FIXTURES

    gt = manifest["fixtures"]["portrait_short"]
    tr = transcribe(FIXTURES / gt["file"], language="en")
    ref = " ".join(s["text"] for s in gt["speech_segments"])
    assert wer(ref, tr.text) < 0.3
    words = tr.words
    assert words and all(0 <= w.start <= w.end <= gt["duration"] + 0.5 for w in words)
