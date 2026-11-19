"""Render clips with ffmpeg: frame-accurate cut, crop to 9:16, scale to 1080x1920."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from clipmaker.media import MediaError, require_tool

OUT_W, OUT_H = 1080, 1920


@dataclass(frozen=True)
class Crop:
    x: int
    y: int
    w: int
    h: int


def center_crop(src_w: int, src_h: int, out_w: int = OUT_W, out_h: int = OUT_H) -> Crop:
    """Largest centered window with the output aspect ratio. A 9:16 input is left whole."""
    target = out_w / out_h
    if abs(src_w / src_h - target) < 0.01:
        return Crop(0, 0, src_w, src_h)
    if src_w / src_h > target:  # too wide: crop the sides
        w = int(round(src_h * target / 2) * 2)
        return Crop((src_w - w) // 2, 0, w, src_h)
    h = int(round(src_w / target / 2) * 2)  # too tall: crop top and bottom
    return Crop(0, (src_h - h) // 2, src_w, h)


def crop_filter(crop: Crop, xy: tuple[str, str] | None = None) -> str:
    """crop filter; `xy` are optional per-frame x/y expressions in clip-local time t."""
    x, y = xy if xy else (crop.x, crop.y)
    return f"crop={crop.w}:{crop.h}:{x}:{y}"


def build_filter(crop: Crop, subtitles: Path | None = None, fonts_dir: Path | None = None,
                 xy: tuple[str, str] | None = None, with_crop: bool = True) -> str:
    chain = ([crop_filter(crop, xy)] if with_crop else []) + [f"scale={OUT_W}:{OUT_H}:flags=lanczos", "setsar=1"]
    if subtitles is not None:
        chain.append(f"subtitles=filename='{_escape(subtitles)}'" + (f":fontsdir='{_escape(fonts_dir)}'" if fonts_dir else ""))
    chain.append("format=yuv420p")
    return ",".join(chain)


def _escape(path: Path) -> str:
    # ffmpeg filter args: escape backslash, colon and quote (Windows paths need this)
    return str(Path(path).as_posix()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_clip(src: Path, dst: Path, start: float, end: float, crop: Crop,
                subtitles: Path | None = None, fonts_dir: Path | None = None,
                ranges: list[list[float]] | None = None, xy: tuple[str, str] | None = None) -> Path:
    """Cut [start, end) from src with re-encoding (not keyframe-snapped).

    With several source-time `ranges` inside [start, end], only those parts are
    kept and joined (dead-air removal). Subtitles must be timed on the output timeline.
    `xy` crop expressions use t = source time - start (the crop runs before trimming).
    """
    ffmpeg = require_tool("ffmpeg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if ranges and len(ranges) > 1:
        look = build_filter(crop, subtitles, fonts_dir, with_crop=False)
        n = len(ranges)
        parts = [f"[0:v]{crop_filter(crop, xy)},split={n}" + "".join(f"[s{i}]" for i in range(n))]
        labels = []
        for i, (a, b) in enumerate(ranges):
            a, b = a - start, b - start
            parts.append(f"[s{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
            parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
            labels.append(f"[v{i}][a{i}]")
        graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(ranges)}:v=1:a=1[cv][ca];[cv]{look}[vout]"
        filter_args = ["-filter_complex", graph, "-map", "[vout]", "-map", "[ca]"]
    else:
        filter_args = ["-vf", build_filter(crop, subtitles, fonts_dir, xy=xy)]
    cmd = [
        ffmpeg, "-v", "error", "-y",
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{max(end - start, 0.1):.3f}",
        *filter_args,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-movflags", "+faststart", str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg render failed: {proc.stderr.strip()[-500:]}")
    return dst
