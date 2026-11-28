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


@pytest.mark.skipif(not _models_available(), reason="whisper model not cached; set CLIPMAKER_TEST_MODELS=1 to download")
def test_cli_end_to_end_portrait(manifest, tmp_path):
    import json

    from clipmaker.cli import main
    from clipmaker.media import probe

    from conftest import FIXTURES

    out = tmp_path / "out"
    assert main([str(FIXTURES / "portrait_short.mp4"), "--out", str(out), "--language", "en"]) == 0
    data = json.loads((out / "clips.json").read_text(encoding="utf-8"))
    assert len(data["clips"]) == 1
    clip = data["clips"][0]
    assert {"start", "end", "text", "score", "file"} <= clip.keys()
    info = probe(out / clip["file"])
    assert (info.width, info.height) == (1080, 1920) and info.has_audio


def test_english_goes_to_moonshine_and_hebrew_stays_on_whisper(monkeypatch):
    from clipmaker.transcribe import choose_engine

    monkeypatch.delenv("CLIPMAKER_EN_ENGINE", raising=False)
    assert choose_engine("en", "base") == "moonshine-base"
    assert choose_engine("he", "base") == "whisper-base"
    assert choose_engine(None, "small") == "whisper-small"
    monkeypatch.setenv("CLIPMAKER_EN_ENGINE", "whisper")
    assert choose_engine("en", "tiny") == "whisper-tiny"
    monkeypatch.setenv("CLIPMAKER_EN_ENGINE", "nope")
    with pytest.raises(ValueError):
        choose_engine("en", "base")


def test_speech_sample_joins_chunks_up_to_the_limit():
    import numpy as np

    from clipmaker.transcribe import SAMPLE_RATE, speech_sample

    chunks = [(0.0, np.ones(SAMPLE_RATE * 20, np.float32)), (25.0, np.ones(SAMPLE_RATE * 20, np.float32))]
    assert len(speech_sample(chunks, 30.0)) == SAMPLE_RATE * 30
    assert len(speech_sample([], 30.0)) == 0


@pytest.mark.skipif(not _models_available(), reason="whisper model not cached; set CLIPMAKER_TEST_MODELS=1 to download")
def test_language_is_detected_and_english_uses_moonshine(manifest, monkeypatch):
    from clipmaker.transcribe import transcribe

    from conftest import FIXTURES

    monkeypatch.delenv("CLIPMAKER_EN_ENGINE", raising=False)
    gt = manifest["fixtures"]["portrait_short"]
    tr = transcribe(FIXTURES / gt["file"])  # no language given
    assert tr.language == "en" and tr.model == "moonshine-base-int8"
    assert wer(" ".join(s["text"] for s in gt["speech_segments"]), tr.text) < 0.3


def test_asr_workers_setting(monkeypatch):
    from clipmaker.transcribe import _recognizer_threads, asr_workers

    monkeypatch.setenv("CLIPMAKER_ASR_WORKERS", "3")
    assert asr_workers(10) == 3 and asr_workers(2) == 2 and asr_workers(0) == 1
    monkeypatch.setenv("CLIPMAKER_ASR_WORKERS", "junk")
    assert 1 <= asr_workers(10) <= 4
    assert _recognizer_threads(8) == 1


@pytest.mark.skipif(not _models_available(), reason="whisper model not cached; set CLIPMAKER_TEST_MODELS=1 to download")
def test_parallel_decoding_matches_one_worker(manifest, monkeypatch):
    """Several recognizers in parallel give the same transcript, in the same order (D-021)."""
    from clipmaker.transcribe import transcribe

    from conftest import FIXTURES

    path = FIXTURES / manifest["fixtures"]["two_speakers"]["file"]
    monkeypatch.setenv("CLIPMAKER_ASR_WORKERS", "1")
    one = transcribe(path, language="en").to_dict()
    monkeypatch.setenv("CLIPMAKER_ASR_WORKERS", "2")
    two = transcribe(path, language="en").to_dict()
    assert one == two and len(one["segments"]) > 3
