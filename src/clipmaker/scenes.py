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


RANGE_PADDING_S = 2.0  # decode a little around each clip so cuts at its edges are found


def detect_scene_cuts(path: str | Path, threshold: float = DEFAULT_THRESHOLD,
                      start: float | None = None, end: float | None = None) -> list[float]:
    """Cut times in seconds of the source. With `start`/`end`, only that range is decoded
    (input seeking, so the cost is proportional to the range, not to the whole video)."""
    ffmpeg = require_tool("ffmpeg")
    seek: list[str] = []
    offset = 0.0
    if start is not None and start > 0:
        seek += ["-ss", f"{start:.3f}"]
        offset = start
    if end is not None:
        seek += ["-t", f"{max(0.0, end - offset):.3f}"]
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", *seek, "-i", str(path), "-an",
         "-vf", f"scale=320:-2,scdet=threshold={threshold}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"scene detection failed: {proc.stderr.strip()[-300:]}")
    return [round(t + offset, 3) for t in parse_scdet(proc.stderr)]


def padded_ranges(spans: list[tuple[float, float]], duration: float | None = None,
                  padding: float = RANGE_PADDING_S) -> list[tuple[float, float]]:
    """Pad each (start, end) span, clamp it to the video, and merge overlaps."""
    out: list[list[float]] = []
    for a, b in sorted(spans):
        a, b = max(0.0, a - padding), b + padding
        if duration:
            b = min(duration, b)
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def detect_scene_cuts_in(path: str | Path, spans: list[tuple[float, float]],
                         duration: float | None = None,
                         threshold: float = DEFAULT_THRESHOLD) -> list[float]:
    """Scene cuts inside the (padded, merged) spans only: the D-013 pipeline order."""
    cuts: list[float] = []
    for a, b in padded_ranges(spans, duration):
        cuts += detect_scene_cuts(path, threshold, start=a, end=b)
    return sorted(set(cuts))
