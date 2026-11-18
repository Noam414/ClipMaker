"""Eval metrics: compare pipeline output (clips.json) with fixture ground truth.

clips.json fields the metrics read (all optional except clips[].start/end):
    transcript.text                       full transcript text (for WER)
    scene_cuts: [seconds, ...]            detected hard cuts in the source
    clips[]: start, end                   source-time range of the clip
             file                         rendered clip, relative to out_dir
             kept_ranges: [[s, e], ...]   source ranges actually kept (after dead-air removal)
             crop_track: [[t, x, y, w, h], ...]  source-time crop window
"""
from __future__ import annotations

import re
import subprocess
import unicodedata
from bisect import bisect_left
from pathlib import Path

BOUNDARY_TOL = 0.5
SCENE_TOL = 0.2
MIN_CLIP_S, MAX_CLIP_S = 5.0, 90.0


# ----------------------------------------------------------------- text / WER
_EN_NUMBERS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
               "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()
_DIGIT_WORDS = {str(i): w for i, w in enumerate(_EN_NUMBERS)}


def normalize_words(text: str) -> list[str]:
    """Lowercase, drop punctuation, spell out small English numbers ("5" -> "five")."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s']", " ", text)  # \w covers Hebrew letters
    words = [w.strip("'") for w in text.split() if w.strip("'")]
    return [_DIGIT_WORDS.get(w, w) for w in words]


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


# ------------------------------------------------------------ timing metrics
def sentence_boundaries(segments: list[dict]) -> tuple[list[float], list[float]]:
    return sorted(s["start"] for s in segments), sorted(s["end"] for s in segments)


def _near(value: float, points: list[float], tol: float) -> bool:
    i = bisect_left(points, value)
    return any(abs(points[k] - value) <= tol for k in (i - 1, i) if 0 <= k < len(points))


def boundary_accuracy(clips: list[dict], segments: list[dict], tol: float = BOUNDARY_TOL) -> float | None:
    if not clips or not segments:
        return None
    starts, ends = sentence_boundaries(segments)
    ok = sum(_near(c["start"], starts, tol) and _near(c["end"], ends, tol) for c in clips)
    return ok / len(clips)


def hook_start_rate(clips: list[dict], segments: list[dict], tol: float = BOUNDARY_TOL) -> float | None:
    hooks = sorted(s["start"] for s in segments if s.get("kind") == "hook")
    if not clips or not segments:
        return None
    return sum(_near(c["start"], hooks, tol) for c in clips) / len(clips)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def dead_air_remaining(clips: list[dict], dead_air: list[dict]) -> float | None:
    if not clips:
        return None
    total = 0.0
    for c in clips:
        ranges = c.get("kept_ranges") or [[c["start"], c["end"]]]
        for s, e in ranges:
            total += sum(_overlap(s, e, d["start"], d["end"]) for d in dead_air)
    return round(total, 3)


def scene_cut_pr(detected: list[float] | None, truth: list[float], tol: float = SCENE_TOL):
    if detected is None or not truth:
        return None, None
    truth_s = sorted(truth)
    det_s = sorted(detected)
    tp_det = sum(_near(d, truth_s, tol) for d in det_s)
    tp_truth = sum(_near(t, det_s, tol) for t in truth_s)
    precision = tp_det / len(det_s) if det_s else 1.0
    return precision, tp_truth / len(truth_s)


def _interp_track(track: list[list[float]], t: float) -> list[float] | None:
    if not track:
        return None
    if t <= track[0][0]:
        return track[0][1:]
    for (t0, *a), (t1, *b) in zip(track, track[1:]):
        if t0 <= t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return [x + (y - x) * f for x, y in zip(a, b)]
    return track[-1][1:]


def face_inside_rate(clips: list[dict], face_track: list[list[float]] | None,
                     face_box: list[float] | None) -> float | None:
    """Share of face_track samples (inside clip ranges) whose face box sits fully inside the crop."""
    if not clips or not face_track or not face_box:
        return None
    fw, fh = face_box
    total = inside = 0
    for c in clips:
        crop = c.get("crop_track")
        if not crop:
            return None
        for t, cx, cy in face_track:
            if not (c["start"] <= t <= c["end"]):
                continue
            x, y, w, h = _interp_track(crop, t)
            total += 1
            inside += (cx - fw / 2 >= x - 1 and cx + fw / 2 <= x + w + 1
                       and cy - fh / 2 >= y - 1 and cy + fh / 2 <= y + h + 1)
    return inside / total if total else None


# ------------------------------------------------------------ output checks
def check_output(path: Path) -> dict:
    from clipmaker.media import MediaError, probe

    try:
        info = probe(path)
    except MediaError as exc:
        return {"valid": False, "reason": str(exc)[:200]}
    problems = []
    if (info.width, info.height) != (1080, 1920):
        problems.append(f"size {info.width}x{info.height}")
    if not info.has_audio:
        problems.append("no audio")
    if not (MIN_CLIP_S <= info.duration <= MAX_CLIP_S):
        problems.append(f"duration {info.duration:.1f}s")
    return {"valid": not problems, "reason": ", ".join(problems) or None, "duration": round(info.duration, 2)}


def contact_sheet(clip: Path, out_png: Path, tiles: int = 6) -> bool:
    from clipmaker.media import probe

    try:
        dur = probe(clip).duration
    except Exception:
        return False
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fps = tiles / max(dur, 0.1)
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(clip),
         "-vf", f"fps={fps:.4f},scale=270:-2,tile={tiles}x1", "-frames:v", "1", str(out_png)],
        capture_output=True,
    )
    return proc.returncode == 0


# ------------------------------------------------------------ per fixture
def score_fixture(gt: dict, result: dict | None, out_dir: Path, elapsed: float,
                  frames_dir: Path | None = None) -> dict:
    duration = gt.get("probed_duration") or gt.get("duration") or 0
    metrics: dict = {
        "wer": None, "boundary_accuracy": None, "hook_start_rate": None,
        "dead_air_remaining_s": None, "face_inside_rate": None,
        "scene_cut_precision": None, "scene_cut_recall": None,
        "valid_output_rate": None, "clip_count": 0,
        "seconds_per_input_minute": round(elapsed / (duration / 60), 2) if duration else None,
    }
    if result is None:
        return {"metrics": metrics, "clips": []}
    segments = gt.get("speech_segments") or []
    clips = result.get("clips") or []
    metrics["clip_count"] = len(clips)

    transcript = (result.get("transcript") or {}).get("text")
    if transcript is not None and segments:
        metrics["wer"] = round(wer(" ".join(s["text"] for s in segments), transcript), 4)
    metrics["boundary_accuracy"] = boundary_accuracy(clips, segments)
    metrics["hook_start_rate"] = hook_start_rate(clips, segments)
    metrics["dead_air_remaining_s"] = dead_air_remaining(clips, gt.get("dead_air") or [])
    metrics["face_inside_rate"] = face_inside_rate(clips, gt.get("face_track"), gt.get("face_box_size"))
    p, r = scene_cut_pr(result.get("scene_cuts"), gt.get("scene_cuts") or [])
    metrics["scene_cut_precision"], metrics["scene_cut_recall"] = p, r

    clip_reports = []
    for c in clips:
        rep = {"start": c["start"], "end": c["end"]}
        if c.get("file"):
            path = out_dir / c["file"]
            rep.update(check_output(path))
            if frames_dir is not None and path.is_file():
                contact_sheet(path, frames_dir / (Path(c["file"]).stem + ".png"))
        clip_reports.append(rep)
    checked = [c for c in clip_reports if "valid" in c]
    if checked:
        metrics["valid_output_rate"] = sum(c["valid"] for c in checked) / len(checked)
    return {"metrics": metrics, "clips": clip_reports}


def aggregate(per_fixture: dict[str, dict]) -> dict:
    keys = ["wer", "boundary_accuracy", "hook_start_rate", "dead_air_remaining_s", "face_inside_rate",
            "scene_cut_precision", "scene_cut_recall", "valid_output_rate", "seconds_per_input_minute"]
    summary: dict = {"fixtures": len(per_fixture),
                     "clip_count": sum(r["metrics"]["clip_count"] for r in per_fixture.values()),
                     "errors": sum(1 for r in per_fixture.values() if r.get("error"))}
    for k in keys:
        vals = [r["metrics"][k] for r in per_fixture.values() if r["metrics"].get(k) is not None]
        if k == "dead_air_remaining_s":
            summary[k] = round(sum(vals), 3) if vals else None
        else:
            summary[k] = round(sum(vals) / len(vals), 4) if vals else None
    return summary
