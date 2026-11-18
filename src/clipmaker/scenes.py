"""Hard scene-cut detection with ffmpeg's scdet filter (on a downscaled copy for speed)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from clipmaker.media import MediaError, require_tool

DEFAULT_THRESHOLD = 10.0  # scdet score (0-100); hard cuts in real footage typically score > 15
MIN_GAP_S = 0.5  # merge detections closer than this (flashes, fades)

_TIME_RE = re.compile(r"lavfi\.scd\.time:\s*([0-9.]+)")


def parse_scdet(log: str, min_gap: float = MIN_GAP_S) -> list[float]:
    cuts: list[float] = []
    for m in _TIME_RE.finditer(log):
        t = round(float(m.group(1)), 3)
        if not cuts or t - cuts[-1] >= min_gap:
            cuts.append(t)
    return cuts


def detect_scene_cuts(path: str | Path, threshold: float = DEFAULT_THRESHOLD) -> list[float]:
    ffmpeg = require_tool("ffmpeg")
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-an",
         "-vf", f"scale=320:-2,scdet=threshold={threshold}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"scene detection failed: {proc.stderr.strip()[-300:]}")
    return parse_scdet(proc.stderr)
