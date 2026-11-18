"""End-to-end pipeline: input video -> clips + clips.json.

Stages so far: probe, transcribe, sentence segmentation + clip selection.
Later M1 items add cutting, reframing and captions.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from clipmaker.media import probe

CLIPS_SCHEMA_VERSION = 1


def run_pipeline(input_path: str | Path, out_dir: str | Path, language: str | None = None,
                 transcribe: bool = True, max_clips: int = 5) -> dict:
    started = time.perf_counter()
    input_path = Path(input_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    info = probe(input_path)
    result = {
        "schema_version": CLIPS_SCHEMA_VERSION,
        "input": {"file": input_path.name, **asdict(info)},
        "transcript": None,
        "clips": [],
        "timings": {},
    }
    t0 = time.perf_counter()
    info_ok = info.has_audio and transcribe
    if info_ok:
        from clipmaker.transcribe import transcribe as run_asr

        result["transcript"] = run_asr(input_path, language=language).to_dict()
    result["timings"]["transcribe_seconds"] = round(time.perf_counter() - t0, 3) if info_ok else None
    if result["transcript"]:
        from dataclasses import asdict as _asdict

        from clipmaker.segment import candidate_clips, select_clips, split_sentences

        sentences = split_sentences(result["transcript"]["segments"])
        result["sentences"] = [_asdict(x) for x in sentences]
        for n, c in enumerate(select_clips(candidate_clips(sentences), max_clips=max_clips), 1):
            result["clips"].append({
                "id": f"clip_{n:02d}", "start": round(c.start, 3), "end": round(c.end, 3),
                "duration": round(c.duration, 3), "text": c.text, "score": c.score,
            })
    result["timings"]["total_seconds"] = round(time.perf_counter() - started, 3)
    (out_dir / "clips.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
