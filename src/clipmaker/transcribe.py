"""Speech-to-text with word timestamps, on CPU.

Approach (see D-002): Silero VAD splits the audio into speech chunks, Whisper
(sherpa-onnx, int8) transcribes each chunk, and word times inside a chunk are
spread proportionally to word length. Chunk edges come from the VAD, so word
timing error is bounded by the chunk, and sentence ends that coincide with a
pause (the common case) land close to the real boundary.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

SAMPLE_RATE = 16000


@dataclass
class Word:
    start: float
    end: float
    word: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    language: str | None
    model: str
    segments: list[Segment]

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments if s.text)

    @property
    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    def to_dict(self) -> dict:
        return {"language": self.language, "model": self.model, "text": self.text,
                "segments": [asdict(s) for s in self.segments]}


def spread_words(text: str, start: float, end: float) -> list[Word]:
    """Assign each whitespace-separated word a time span proportional to its length."""
    tokens = text.split()
    if not tokens:
        return []
    weights = [len(t) + 1 for t in tokens]  # +1 approximates the inter-word gap
    total = sum(weights)
    span = max(end - start, 0.0)
    words, t = [], start
    for tok, w in zip(tokens, weights):
        dur = span * w / total
        words.append(Word(round(t, 3), round(t + dur, 3), tok))
        t += dur
    words[-1].end = round(end, 3)
    return words


def load_audio(path: str | Path):
    import numpy as np

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio decode failed: {proc.stderr.decode(errors='replace')[:300]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def vad_chunks(audio, vad_model: Path, max_chunk_s: float = 20.0) -> list[tuple[float, object]]:
    import numpy as np
    import sherpa_onnx

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(vad_model)
    cfg.silero_vad.min_silence_duration = 0.3
    cfg.silero_vad.min_speech_duration = 0.2
    cfg.silero_vad.max_speech_duration = max_chunk_s
    cfg.sample_rate = SAMPLE_RATE
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=max(60, int(len(audio) / SAMPLE_RATE) + 5))
    chunks: list[tuple[float, object]] = []

    def drain():
        while not vad.empty():
            chunks.append((vad.front.start / SAMPLE_RATE, np.array(vad.front.samples, dtype=np.float32)))
            vad.pop()

    window = cfg.silero_vad.window_size or 512
    for i in range(0, len(audio), window):
        vad.accept_waveform(audio[i:i + window])
        drain()
    vad.flush()
    drain()
    return chunks


def transcribe(path: str | Path, language: str | None = None, model: str | None = None) -> Transcript:
    import sherpa_onnx

    from clipmaker import models

    model = model or os.environ.get("CLIPMAKER_WHISPER_MODEL", "base")
    files = models.whisper(model)
    recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
        encoder=str(files["encoder"]), decoder=str(files["decoder"]), tokens=str(files["tokens"]),
        language=language or "", task="transcribe",
        num_threads=max(1, min(4, os.cpu_count() or 1)),
    )
    audio = load_audio(path)
    segments: list[Segment] = []
    for start, samples in vad_chunks(audio, models.silero_vad()):
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        recognizer.decode_stream(stream)
        text = stream.result.text.strip()
        if not text:
            continue
        end = start + len(samples) / SAMPLE_RATE
        segments.append(Segment(round(start, 3), round(end, 3), text, spread_words(text, start, end)))
    return Transcript(language=language, model=f"whisper-{model}-int8", segments=segments)
