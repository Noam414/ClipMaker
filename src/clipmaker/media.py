"""Thin wrappers around ffmpeg / ffprobe."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    duration: float
    width: int | None
    height: int | None
    has_video: bool
    has_audio: bool
    fps: float | None


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise MediaError(f"{name} not found on PATH; install ffmpeg (https://ffmpeg.org/download.html)")
    return path


def _parse_rate(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    num, _, den = rate.partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: str | Path) -> ProbeResult:
    ffprobe = require_tool("ffprobe")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffprobe failed for {path}: {proc.stderr.strip()[:500]}")
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or (video or {}).get("duration") or 0.0)
    return ProbeResult(
        duration=duration,
        width=int(video["width"]) if video else None,
        height=int(video["height"]) if video else None,
        has_video=video is not None,
        has_audio=audio is not None,
        fps=_parse_rate(video.get("avg_frame_rate")) if video else None,
    )
