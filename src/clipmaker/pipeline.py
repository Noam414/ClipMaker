"""End-to-end pipeline: input video -> clips + clips.json.

M0 skeleton: probes the input and writes an empty clips.json. Later M1 items
fill in transcription, segmentation, cutting, reframing and captions.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from clipmaker.media import probe

CLIPS_SCHEMA_VERSION = 1


def run_pipeline(input_path: str | Path, out_dir: str | Path) -> dict:
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
    result["timings"]["total_seconds"] = round(time.perf_counter() - started, 3)
    (out_dir / "clips.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
