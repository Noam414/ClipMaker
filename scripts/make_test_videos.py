#!/usr/bin/env python3
"""Generate synthetic test videos plus exact ground truth for ClipMaker evals.

Everything is generated locally (no downloads), so it works inside a cloud VM
with restricted network access. Stdlib only.

Requirements on PATH: ffmpeg, ffprobe, espeak-ng
    apt-get install -y ffmpeg espeak-ng

Usage:
    python3 scripts/make_test_videos.py                # writes tests/fixtures/videos/
    python3 scripts/make_test_videos.py --out DIR --force

Output:
    <out>/<name>.mp4         one file per fixture
    <out>/manifest.json      ground truth for every fixture:
                             speech segments with exact start/end + text,
                             dead-air ranges, hard scene cuts, and the
                             "face" position over time (for reframing evals).
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "tests" / "fixtures" / "videos"

# --------------------------------------------------------------------------- #
# Fixture scripts. Each item is (text, pause_after_seconds).
# A pause >= 2.0 s is recorded as dead air the pipeline should be able to trim.
# Sentences flagged "hook" are the ones a good highlight detector should rank
# highest; "filler" ones are the ones it should avoid starting a clip on.
# --------------------------------------------------------------------------- #
PODCAST_EN = [
    ("Welcome back to the show. Today we are talking about building habits.", 0.5, "filler"),
    ("Um, so, before we start, let me check the microphone levels.", 0.6, "filler"),
    ("Okay. Here is the thing nobody tells you about motivation.", 0.4, "hook"),
    ("Motivation is not the cause of action. It is the result of action.", 0.4, "hook"),
    ("You do not wait to feel ready. You start, and the feeling follows.", 0.8, "hook"),
    ("I learned this the hard way after quitting the gym four times in one year.", 0.5, "story"),
    ("Every time, I waited for the perfect Monday. It never came.", 3.0, "story"),
    ("Sorry, I lost my notes for a second there.", 0.6, "filler"),
    ("So here is the simple rule I use now. Two minutes.", 0.4, "hook"),
    ("If a habit takes less than two minutes to start, you have no excuse to skip it.", 0.4, "hook"),
    ("Put on your running shoes. That is the whole habit on day one.", 0.5, "hook"),
    ("The results surprised me. Six months later I had not missed a single week.", 0.6, "story"),
    ("Anyway, that is it for today. Thanks for listening, and see you next time.", 0.5, "filler"),
]

PORTRAIT_EN = [
    ("Stop scrolling. This one trick will save you an hour every day.", 0.4, "hook"),
    ("Batch your messages. Answer them twice a day, not every five minutes.", 0.4, "hook"),
    ("Your focus will thank you. Try it tomorrow morning.", 0.5, "story"),
]

HEBREW = [
    ("שלום לכולם, היום נדבר על איך ללמוד מהר יותר.", 0.5, "filler"),
    ("הסוד הוא לא ללמוד יותר שעות, אלא ללמוד בהפסקות קצרות.", 0.4, "hook"),
    ("עשרים וחמש דקות של ריכוז, ואחר כך חמש דקות מנוחה.", 0.5, "hook"),
    ("נסו את זה מחר ותראו את ההבדל.", 0.5, "story"),
]

FPS = 30


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(" ".join(cmd) + "\n" + proc.stderr[-3000:] + "\n")
        raise SystemExit(f"command failed: {cmd[0]}")


def require_tools() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe", "espeak-ng") if shutil.which(t) is None]
    if missing:
        raise SystemExit(
            f"missing tools: {', '.join(missing)}\n"
            "install with: apt-get update && apt-get install -y ffmpeg espeak-ng"
        )


def has_drawtext() -> bool:
    """drawtext needs libfreetype + a font; skip the overlay if unavailable."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=s=64x64:d=0.1", "-vf", "drawtext=text=x", "-f", "null", "-"],
        capture_output=True,
    )
    return proc.returncode == 0


def synth_speech(lines, voice: str, tmp: Path, speed: int = 165):
    """Render each sentence with espeak-ng, join with exact silences.

    Returns (wav_path, duration_s, segments, dead_air).
    """
    frames = bytearray()
    params = None
    t = 0.0
    segments, dead_air = [], []
    for i, (text, pause, kind) in enumerate(lines):
        part = tmp / f"{voice}_{i}.wav"
        run(["espeak-ng", "-v", voice, "-s", str(speed), "-w", str(part), text])
        with wave.open(str(part)) as w:
            if params is None:
                params = w.getparams()
            data = w.readframes(w.getnframes())
            dur = w.getnframes() / w.getframerate()
        frames += data
        segments.append({"start": round(t, 3), "end": round(t + dur, 3), "text": text, "kind": kind})
        t += dur
        n_sil = int(round(pause * params.framerate))
        frames += b"\x00" * (n_sil * params.sampwidth * params.nchannels)
        if pause >= 2.0:
            dead_air.append({"start": round(t, 3), "end": round(t + pause, 3)})
        t += n_sil / params.framerate
    out = tmp / f"{voice}_full.wav"
    with wave.open(str(out), "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(frames))
    return out, round(t, 3), segments, dead_air


def face_track(duration: float, cx_expr, cy_expr, step: float = 0.5):
    pts = []
    t = 0.0
    while t <= duration + 1e-9:
        pts.append([round(t, 2), round(cx_expr(t), 1), round(cy_expr(t), 1)])
        t += step
    return pts


def draw_face(path: Path, w: int, h: int) -> bool:
    """Draw a simple cartoon face (hair, skin, eyes, brows, nose, mouth) that a real face
    detector (YuNet) recognises. Returns False if OpenCV isn't installed."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return False
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (56, 42, 30)  # same as the background colour 0x1E2A38 (BGR)
    cx, cy = w // 2, h // 2
    cv2.ellipse(img, (cx, cy + h // 20), (int(w * 0.40), int(h * 0.44)), 0, 0, 360, (40, 50, 70), -1)
    cv2.ellipse(img, (cx, cy + h // 12), (int(w * 0.36), int(h * 0.40)), 0, 0, 360, (150, 185, 230), -1)
    for s in (-1, 1):
        ex, ey = cx + s * int(w * 0.15), cy - int(h * 0.02)
        cv2.ellipse(img, (ex, ey), (int(w * 0.075), int(h * 0.035)), 0, 0, 360, (250, 250, 250), -1)
        cv2.circle(img, (ex, ey), int(w * 0.035), (60, 40, 30), -1)
        cv2.circle(img, (ex, ey), int(w * 0.015), (10, 10, 10), -1)
        cv2.line(img, (ex - int(w * 0.08), ey - int(h * 0.07)), (ex + int(w * 0.08), ey - int(h * 0.08)),
                 (40, 50, 70), max(2, w // 40))
    nose = np.array([[cx, cy + int(h * 0.02)], [cx - int(w * 0.05), cy + int(h * 0.17)],
                     [cx + int(w * 0.05), cy + int(h * 0.17)]])
    cv2.polylines(img, [nose], False, (110, 140, 190), max(2, w // 60))
    cv2.ellipse(img, (cx, cy + int(h * 0.27)), (int(w * 0.12), int(h * 0.04)), 0, 0, 180, (90, 90, 180),
                max(3, w // 30))
    return bool(cv2.imwrite(str(path), img))


def talking_head(name, out_dir, wav, duration, w, h, face_w, face_h, cx_ffmpeg, cy_ffmpeg, text_ok):
    """A flat background with a cartoon 'face' (a skin-coloured box if OpenCV is missing)
    and a 'body' box that move.

    Uses overlay (evaluated per frame) rather than drawbox, whose position
    expressions are only evaluated once.
    """
    body_w, body_h = face_w * 2, h // 3
    face_png = Path(tempfile.gettempdir()) / f"clipmaker_face_{face_w}x{face_h}.png"
    if draw_face(face_png, face_w, face_h):
        face_input = ["-loop", "1", "-framerate", str(FPS), "-t", str(duration), "-i", str(face_png)]
    else:
        face_input = ["-f", "lavfi", "-i", f"color=c=0xF2C9A0:s={face_w}x{face_h}:r={FPS}:d={duration}"]
    graph = (
        f"[0:v][2:v]overlay=x='({cx_ffmpeg})-{body_w}/2':y='({cy_ffmpeg})+{face_h}/2+20':shortest=1[b];"
        f"[b][1:v]overlay=x='({cx_ffmpeg})-{face_w}/2':y='({cy_ffmpeg})-{face_h}/2':shortest=1"
    )
    if text_ok:
        graph += f",drawtext=text='{name} %{{pts\\:hms}}':x=20:y=20:fontsize={h // 30}:fontcolor=white"
    graph += "[v]"
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=0x1E2A38:s={w}x{h}:r={FPS}:d={duration}",
        *face_input,
        "-f", "lavfi", "-i", f"color=c=0x3B6EA5:s={body_w}x{body_h}:r={FPS}:d={duration}",
        "-i", str(wav),
        "-filter_complex", graph, "-map", "[v]", "-map", "3:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-shortest",
        str(out_dir / f"{name}.mp4"),
    ])


def scene_cuts(out_dir, text_ok):
    """Five 6-second scenes with hard cuts, each with its own tone."""
    srcs = [
        "testsrc2=s=1280x720:r=30",
        "mandelbrot=s=1280x720:r=30",
        "smptebars=s=1280x720:r=30",
        "life=s=1280x720:r=30:mold=10:ratio=0.1:death_color=#202020:life_color=#40C040",
        "color=c=0x8B1E3F:s=1280x720:r=30",
    ]
    seg = 6
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for s in srcs:
        cmd += ["-f", "lavfi", "-t", str(seg), "-i", s]
    for i in range(len(srcs)):
        cmd += ["-f", "lavfi", "-t", str(seg), "-i", f"sine=frequency={220 * (i + 1)}:sample_rate=44100"]
    n = len(srcs)
    chains = "".join(f"[{i}:v]format=yuv420p,setsar=1[v{i}];" for i in range(n))
    concat = "".join(f"[v{i}][{n + i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    cmd += ["-filter_complex", chains + concat, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", str(out_dir / "scene_cuts.mp4")]
    run(cmd)
    return {"duration": seg * n, "width": 1280, "height": 720,
            "scene_cuts": [seg * i for i in range(1, n)], "speech_segments": [], "dead_air": []}


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return round(float(out), 3)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="regenerate even if manifest exists")
    args = ap.parse_args()

    require_tools()
    out = args.out
    manifest_path = out / "manifest.json"
    if manifest_path.exists() and not args.force:
        print(f"fixtures already exist at {out} (use --force to regenerate)")
        return
    out.mkdir(parents=True, exist_ok=True)
    text_ok = has_drawtext()
    manifest: dict = {"version": 1, "fps": FPS, "fixtures": {}}

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1. Landscape podcast: moving face, dead air, hooks vs filler.
        wav, dur, segs, dead = synth_speech(PODCAST_EN, "en-us", tmp)
        talking_head("podcast_landscape", out, wav, dur, 1920, 1080, 220, 280,
                     "960+400*sin(2*PI*t/20)", "420", text_ok)
        manifest["fixtures"]["podcast_landscape"] = {
            "file": "podcast_landscape.mp4", "language": "en", "duration": dur,
            "width": 1920, "height": 1080, "speech_segments": segs, "dead_air": dead,
            "scene_cuts": [],
            "face_box_size": [220, 280],
            "face_track": face_track(dur, lambda t: 960 + 400 * math.sin(2 * math.pi * t / 20), lambda t: 420),
            "notes": "Best clips should start on a 'hook' segment and end on a sentence boundary. "
                     "9:16 reframing should keep face_track inside the crop.",
        }

        # 2. Portrait input: already 9:16, must not be cropped again.
        wav, dur, segs, dead = synth_speech(PORTRAIT_EN, "en-us", tmp, speed=175)
        talking_head("portrait_short", out, wav, dur, 1080, 1920, 300, 380,
                     "540", "700+60*sin(2*PI*t/8)", text_ok)
        manifest["fixtures"]["portrait_short"] = {
            "file": "portrait_short.mp4", "language": "en", "duration": dur,
            "width": 1080, "height": 1920, "speech_segments": segs, "dead_air": dead,
            "scene_cuts": [],
            "face_box_size": [300, 380],
            "face_track": face_track(dur, lambda t: 540, lambda t: 700 + 60 * math.sin(2 * math.pi * t / 8)),
            "notes": "Input is already vertical; output should keep full frame.",
        }

        # 3. Hebrew speech: right-to-left captions must render correctly.
        wav, dur, segs, dead = synth_speech(HEBREW, "he", tmp, speed=150)
        talking_head("hebrew_talk", out, wav, dur, 1920, 1080, 220, 280,
                     "960-250*sin(2*PI*t/12)", "430", text_ok)
        manifest["fixtures"]["hebrew_talk"] = {
            "file": "hebrew_talk.mp4", "language": "he", "duration": dur,
            "width": 1920, "height": 1080, "speech_segments": segs, "dead_air": dead,
            "scene_cuts": [],
            "face_box_size": [220, 280],
            "face_track": face_track(dur, lambda t: 960 - 250 * math.sin(2 * math.pi * t / 12), lambda t: 430),
            "notes": "Captions are RTL Hebrew; check word order and punctuation placement visually.",
        }

        # 4. Hard scene cuts, no speech.
        manifest["fixtures"]["scene_cuts"] = {"file": "scene_cuts.mp4", "language": None,
                                              **scene_cuts(out, text_ok),
                                              "notes": "Scene detection should find cuts at 6/12/18/24 s."}

    for name, fx in manifest["fixtures"].items():
        fx["probed_duration"] = probe_duration(out / fx["file"])

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum((out / f["file"]).stat().st_size for f in manifest["fixtures"].values())
    print(f"wrote {len(manifest['fixtures'])} fixtures ({total / 1e6:.1f} MB) + manifest.json to {out}")


if __name__ == "__main__":
    main()
