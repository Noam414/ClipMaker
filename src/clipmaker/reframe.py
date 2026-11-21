"""Speaker-following 9:16 reframing on CPU.

1. Sample low-resolution frames of the clip's source range (a few per second).
2. Find the subject in each frame: YuNet faces (OpenCV, MIT model) first; if the
   detector finds no face anywhere in the clip, fall back to the largest skin-coloured
   region. With two or more people: frame them together if they fit in the crop,
   otherwise follow the active speaker (most lower-face motion, with hysteresis).
3. Build a camera path: fill gaps, smooth with a zero-lag Gaussian, separately for each
   shot (the path resets at scene cuts), clamp to the frame and make sure the subject
   box stays inside the crop.
4. Simplify the path to keyframes; ffmpeg's crop filter follows them through a
   piecewise-linear expression.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from clipmaker.media import MediaError, require_tool
from clipmaker.render import Crop

log = logging.getLogger(__name__)

SAMPLE_FPS = 4.0
ANALYSIS_W = 480
SMOOTH_SIGMA_S = 0.5
FACE_MIN_SHARE = 0.2  # share of samples with a YuNet face needed to trust faces only


@dataclass(frozen=True)
class Box:
    cx: float
    cy: float
    w: float
    h: float


def sample_frames(src: Path, start: float, end: float, src_w: int, src_h: int,
                  fps: float = SAMPLE_FPS) -> tuple[list[float], list[np.ndarray], float]:
    """Return (source times, BGR frames, scale) for [start, end) at `fps`, downscaled."""
    ffmpeg = require_tool("ffmpeg")
    scale = min(1.0, ANALYSIS_W / src_w)
    w, h = int(round(src_w * scale / 2) * 2), int(round(src_h * scale / 2) * 2)
    cmd = [ffmpeg, "-v", "error", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
           "-vf", f"fps={fps},scale={w}:{h}", "-an", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise MediaError(f"frame sampling failed: {proc.stderr.decode(errors='replace')[-300:]}")
    n = len(proc.stdout) // (w * h * 3)
    frames = [np.frombuffer(proc.stdout, np.uint8, w * h * 3, i * w * h * 3).reshape(h, w, 3) for i in range(n)]
    times = [start + (i + 0.5) / fps for i in range(n)]
    return times, frames, w / src_w


class FaceDetector:
    """YuNet via cv2.FaceDetectorYN; unavailable (returns []) if OpenCV or the model is missing."""

    def __init__(self) -> None:
        self._det = None
        try:
            import cv2

            from clipmaker.models import yunet

            self._det = cv2.FaceDetectorYN.create(str(yunet()), "", (320, 320), 0.7, 0.3, 50)
        except Exception as exc:  # no network, no OpenCV: skin fallback only
            log.warning("face detector unavailable, using skin-colour fallback: %s", exc)

    @property
    def available(self) -> bool:
        return self._det is not None

    def detect(self, frame: np.ndarray) -> list[Box]:
        if self._det is None:
            return []
        h, w = frame.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(frame)
        if faces is None:
            return []
        return [Box(f[0] + f[2] / 2, f[1] + f[3] / 2, f[2], f[3]) for f in faces]


def skin_box(frame: np.ndarray, min_share: float = 0.005) -> Box | None:
    """Bounding box of the largest skin-coloured blob (YCrCb thresholds), or None."""
    f = frame.astype(np.float32)
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cr = (r - y) * 0.713 + 128
    cb = (b - y) * 0.564 + 128
    mask = (cr > 135) & (cr < 175) & (cb > 80) & (cb < 130) & (y > 60)
    if mask.mean() < min_share:
        return None
    try:
        import cv2

        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        if n < 2:
            return None
        i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        x, y0, w, h = (float(v) for v in stats[i, :4])
    except ImportError:
        ys, xs = np.nonzero(mask)
        x, y0, w, h = float(xs.min()), float(ys.min()), float(xs.ptp() + 1), float(ys.ptp() + 1)
    return Box(x + w / 2, y0 + h / 2, w, h)


def face_tracks(faces: list[list[Box]], min_share: float = 0.25) -> list[list[Box | None]]:
    """Group per-frame faces into tracks by position (greedy nearest match).

    Returns only tracks present in at least `min_share` of frames, biggest first."""
    tracks: list[dict] = []
    for i, frame_faces in enumerate(faces):
        used: set[int] = set()
        for b in sorted(frame_faces, key=lambda f: -f.w * f.h):
            best, best_d = None, None
            for k, tr in enumerate(tracks):
                d = abs(tr["last"].cx - b.cx) + abs(tr["last"].cy - b.cy)
                if k not in used and d < max(b.w, b.h) and (best_d is None or d < best_d):
                    best, best_d = k, d
            if best is None:
                tracks.append({"boxes": {}, "last": b})
                best = len(tracks) - 1
            tracks[best]["boxes"][i] = b
            tracks[best]["last"] = b
            used.add(best)
    n = len(faces)
    keep = [t for t in tracks if n and len(t["boxes"]) / n >= min_share]
    keep.sort(key=lambda t: -np.mean([b.w * b.h for b in t["boxes"].values()]))
    return [[t["boxes"].get(i) for i in range(n)] for t in keep]


def mouth_activity(frames: list[np.ndarray], track: list[Box | None]) -> np.ndarray:
    """Mean absolute change of the lower-face region between consecutive samples."""
    out = np.zeros(len(frames))
    for i in range(1, len(frames)):
        b = track[i] or track[i - 1]
        if b is None:
            continue
        h, w = frames[i].shape[:2]
        x0, x1 = int(max(b.cx - 0.3 * b.w, 0)), int(min(b.cx + 0.3 * b.w, w))
        y0, y1 = int(max(b.cy + 0.1 * b.h, 0)), int(min(b.cy + 0.5 * b.h, h))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        a = frames[i][y0:y1, x0:x1].astype(np.int16).mean(axis=2)
        p = frames[i - 1][y0:y1, x0:x1].astype(np.int16).mean(axis=2)
        out[i] = float(np.abs(a - p).mean())
    return out


def pick_speaker(activity: list[np.ndarray], fps: float = SAMPLE_FPS, window_s: float = 1.0,
                 margin: float = 1.5, hold_s: float = 0.75) -> list[int]:
    """Index of the active track per sample, with hysteresis: switch only when another
    track is clearly more active for `hold_s`; the switch is back-dated to when that began."""
    n = len(activity[0])
    k = max(1, int(round(window_s * fps)))
    smooth = [np.convolve(a, np.ones(k) / k, mode="same") for a in activity]
    cur = int(np.argmax([sm[: k + 1].sum() for sm in smooth]))
    out = [cur] * n
    run_start, run_who = None, None
    hold = max(1, int(round(hold_s * fps)))
    for i in range(n):
        best = int(np.argmax([sm[i] for sm in smooth]))
        if best != cur and smooth[best][i] > margin * smooth[cur][i] + 0.2:
            if run_who != best:
                run_start, run_who = i, best
            if i - run_start + 1 >= hold:
                cur = best
                for j in range(run_start, i + 1):
                    out[j] = cur
                run_start, run_who = None, None
        else:
            run_start, run_who = None, None
        out[i] = cur
    return out


def detect_subjects(frames: list[np.ndarray], detector: FaceDetector | None = None,
                    info: dict | None = None, crop_frac: float = 9 / 16 * 9 / 16) -> list[Box | None]:
    """One subject box per frame (analysis pixels), or None where nothing was found.

    Sets `info["source"]` ("face", "speakers", "group" or "skin") and, when following
    the active speaker, `info["switches"]` (sample indexes where the speaker changes).
    `crop_frac` is crop width / frame width: two faces that fit together are framed together.
    """
    info = {} if info is None else info
    info["switches"] = []
    faces = [detector.detect(f) if detector else [] for f in frames]
    found = sum(1 for x in faces if x)
    if not frames or found / len(frames) < FACE_MIN_SHARE:
        info["source"] = "skin"
        return [skin_box(f) for f in frames]
    tracks = face_tracks(faces)
    if len(tracks) < 2:
        info["source"] = "face"
        return [max(x, key=lambda b: b.w * b.h) if x else None for x in faces]
    tracks = tracks[:4]
    width = frames[0].shape[1]
    span = [max(b.cx + b.w / 2 for b in x) - min(b.cx - b.w / 2 for b in x) if len(x) > 1 else 0 for x in faces]
    if np.median(span) <= 0.9 * crop_frac * width:  # everyone fits: frame the group
        info["source"] = "group"
        out = []
        for x in faces:
            if not x:
                out.append(None)
                continue
            lo, hi = min(b.cx - b.w / 2 for b in x), max(b.cx + b.w / 2 for b in x)
            top, bot = min(b.cy - b.h / 2 for b in x), max(b.cy + b.h / 2 for b in x)
            out.append(Box((lo + hi) / 2, (top + bot) / 2, hi - lo, bot - top))
        return out
    info["source"] = "speakers"
    who = pick_speaker([mouth_activity(frames, t) for t in tracks])
    info["switches"] = [i for i in range(1, len(who)) if who[i] != who[i - 1]]
    out = []
    for i, k in enumerate(who):
        b = tracks[k][i]
        if b is None:  # nearest sighting of that person
            seen = [j for j, x in enumerate(tracks[k]) if x is not None]
            b = tracks[k][min(seen, key=lambda j: abs(j - i))]
        out.append(b)
    return out


def _gaussian_smooth(values: np.ndarray, sigma: float) -> np.ndarray:
    if len(values) < 3 or sigma <= 0:
        return values.copy()
    radius = int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k /= k.sum()
    padded = np.pad(values, radius, mode="edge")
    return np.convolve(padded, k, mode="valid")


def camera_path(times: list[float], centers: list[float | None], sizes: list[float | None],
                crop_len: float, frame_len: float, cuts: list[float] = (),
                fps: float = SAMPLE_FPS, sigma_s: float = SMOOTH_SIGMA_S) -> list[float]:
    """Crop offsets (along the cropped axis, source pixels) for each sample time.

    `centers`/`sizes` are the subject's centre and extent on that axis (None = not found).
    The path is smoothed per shot, so it jumps cleanly at a scene cut instead of panning.
    """
    n = len(times)
    if n == 0:
        return []
    max_off = max(frame_len - crop_len, 0.0)
    shots, cur = [], [0]
    cuts = sorted(cuts)
    for i in range(1, n):
        if any(times[i - 1] < c <= times[i] for c in cuts):
            shots.append(cur)
            cur = []
        cur.append(i)
    shots.append(cur)
    out = np.zeros(n)
    for shot in shots:
        c = np.array([np.nan if centers[i] is None else centers[i] for i in shot], dtype=float)
        if np.all(np.isnan(c)):
            c[:] = frame_len / 2
        else:  # hold the nearest detection over gaps
            idx = np.arange(len(c))
            ok = ~np.isnan(c)
            c = np.interp(idx, idx[ok], c[ok])
        smooth = _gaussian_smooth(c, sigma_s * fps)
        off = np.clip(smooth - crop_len / 2, 0, max_off)
        for j, i in enumerate(shot):  # keep the whole subject inside the crop when it fits
            size = sizes[i]
            if centers[i] is None or size is None or size > crop_len:
                continue
            lo, hi = centers[i] - size / 2, centers[i] + size / 2
            if lo < off[j]:
                off[j] = max(lo, 0)
            elif hi > off[j] + crop_len:
                off[j] = min(hi - crop_len, max_off)
        out[shot] = off
    return out.tolist()


def simplify(points: list[tuple[float, float]], tol: float = 2.0) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker on (t, value) with a vertical tolerance in pixels."""
    if len(points) <= 2:
        return list(points)
    (t0, v0), (t1, v1) = points[0], points[-1]
    worst, wi = -1.0, 0
    for i in range(1, len(points) - 1):
        t, v = points[i]
        lin = v0 + (v1 - v0) * (t - t0) / (t1 - t0) if t1 > t0 else v0
        if abs(v - lin) > worst:
            worst, wi = abs(v - lin), i
    if worst <= tol:
        return [points[0], points[-1]]
    return simplify(points[: wi + 1], tol)[:-1] + simplify(points[wi:], tol)


def crop_expression(keys: list[tuple[float, float]], t0: float) -> str:
    """ffmpeg expression in clip-local time `t` (source time - t0), piecewise linear."""
    if len(keys) == 1 or all(abs(v - keys[0][1]) < 0.5 for _, v in keys):
        return f"{keys[0][1]:.1f}"
    k = [(t - t0, v) for t, v in keys]
    terms = [f"lt(t,{k[0][0]:.3f})*{k[0][1]:.1f}"]
    for (ta, va), (tb, vb) in zip(k, k[1:]):
        slope = (vb - va) / (tb - ta) if tb > ta else 0.0
        terms.append(f"gte(t,{ta:.3f})*lt(t,{tb:.3f})*({va:.1f}+{slope:.3f}*(t-{ta:.3f}))")
    terms.append(f"gte(t,{k[-1][0]:.3f})*{k[-1][1]:.1f}")
    return "+".join(terms)


@dataclass
class Reframe:
    crop: Crop                       # w/h fixed; x/y are the first keyframe
    keys: list[tuple[float, float]]  # (source time, offset on the moving axis)
    axis: str                        # "x", "y" or "" (static)
    source: str = "none"             # "face", "speakers", "group", "skin" or "none"

    def track(self) -> list[list[float]]:
        """clips.json crop_track: [[t, x, y, w, h], ...] in source time."""
        if not self.axis:
            return [[self.keys[0][0], self.crop.x, self.crop.y, self.crop.w, self.crop.h]]
        return [[round(t, 3), round(v) if self.axis == "x" else self.crop.x,
                 round(v) if self.axis == "y" else self.crop.y, self.crop.w, self.crop.h]
                for t, v in self.keys]

    def filter_xy(self, t0: float) -> tuple[str, str]:
        if not self.axis:
            return str(self.crop.x), str(self.crop.y)
        expr = f"'{crop_expression(self.keys, t0)}'"
        return (expr, str(self.crop.y)) if self.axis == "x" else (str(self.crop.x), expr)


def plan_reframe(src: Path, start: float, end: float, src_w: int, src_h: int, base: Crop,
                 cuts: list[float] = (), detector: FaceDetector | None = None) -> Reframe:
    """Follow the subject inside [start, end); `base` is the centred crop window."""
    axis = "x" if base.w < src_w else ("y" if base.h < src_h else "")
    if not axis:
        return Reframe(base, [(start, 0.0)], "")
    times, frames, scale = sample_frames(src, start, end, src_w, src_h)
    if not frames:
        return Reframe(base, [(start, float(base.x if axis == "x" else base.y))], axis)
    info: dict = {}
    frac = (base.w / src_w) if axis == "x" else (base.h / src_h)
    boxes = detect_subjects(frames, detector, info, crop_frac=frac)
    centers = [None if b is None else (b.cx if axis == "x" else b.cy) / scale for b in boxes]
    sizes = [None if b is None else (b.w if axis == "x" else b.h) / scale for b in boxes]
    crop_len, frame_len = (base.w, src_w) if axis == "x" else (base.h, src_h)
    # a change of speaker is a hard cut for the virtual camera, like a scene cut
    switch_ts = [(times[i - 1] + times[i]) / 2 for i in info.get("switches", [])]
    cuts = sorted([c for c in cuts if start < c < end] + switch_ts)
    offs = camera_path(times, centers, sizes, crop_len, frame_len, cuts)
    pts = [(start, offs[0])] + list(zip(times, offs)) + [(end, offs[-1])]
    # a cut is a jump: insert a point just before it so the linear segment doesn't pan
    for c in cuts:
        if start < c < end:
            before = [p for p in pts if p[0] < c]
            after = [p for p in pts if p[0] > c]
            if before and after:
                pts += [(c - 0.001, before[-1][1]), (c, after[0][1])]
    pts.sort()
    keys = simplify(pts, tol=2.0)
    found = any(b is not None for b in boxes)
    return Reframe(base, keys, axis, info.get("source", "none") if found else "none")
