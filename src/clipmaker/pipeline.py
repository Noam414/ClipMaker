"""End-to-end pipeline: input video -> clips + clips.json.

Stages (D-013 order): probe, transcribe (audio only), sentence segmentation + clip selection,
scene-cut detection on the selected ranges only, dead-air removal, frame-accurate cut with a
speaker-following 9:16 crop and burned-in captions.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from clipmaker.media import probe

CLIPS_SCHEMA_VERSION = 1


def render_workers(n_clips: int) -> int:
    """Parallel clip renders: `CLIPMAKER_RENDER_WORKERS`, else one per CPU core (max 4)."""
    env = os.environ.get("CLIPMAKER_RENDER_WORKERS", "").strip()
    wanted = int(env) if env.isdigit() and int(env) > 0 else min(4, os.cpu_count() or 1)
    return max(1, min(wanted, n_clips))


def run_pipeline(input_path: str | Path, out_dir: str | Path, language: str | None = None,
                 transcribe: bool = True, max_clips: int = 5,
                 render: bool = True, captions: bool = True,
                 remove_dead_air: bool = True, reframe: bool = True,
                 caption_style: str = "bold", keywords: bool = True, emoji: bool = False,
                 hook_title: bool = True, progress_bar: bool = True, zoom: bool = True,
                 progress: Callable[[str, float], None] | None = None,
                 on_clip: Callable[[dict], None] | None = None,
                 video_source: Callable[[dict], tuple[Path, float]] | None = None) -> dict:
    """`progress(stage, fraction)` is called as the stages advance (fraction 0..1).
    `on_clip(clip)` is called with each clip's clips.json entry as soon as that clip is
    rendered (from the calling thread), so a UI can show clips one by one.

    `video_source(clip)` (M5.5 sections mode): `input_path` is then audio only, and the
    video of each selected clip comes from `video_source`, which returns a local file and
    that file's start time in the source (e.g. a platform link's padded section). Scene
    detection, reframing and render then run on that file, with times shifted back."""
    started = time.perf_counter()
    report = progress or (lambda stage, frac: None)
    report("probe", 0.0)
    input_path = Path(input_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    info = probe(input_path)
    result = {
        "schema_version": CLIPS_SCHEMA_VERSION,
        "input": {"file": input_path.name, **asdict(info)},
        "transcript": None,
        "scene_cuts": None,
        "clips": [],
        "timings": {},
    }
    t0 = time.perf_counter()
    info_ok = info.has_audio and transcribe
    if info_ok:
        from clipmaker.transcribe import transcribe as run_asr

        report("transcribe", 0.02)

        result["transcript"] = run_asr(input_path, language=language).to_dict()
    result["timings"]["transcribe_seconds"] = round(time.perf_counter() - t0, 3) if info_ok else None
    if result["transcript"]:
        from dataclasses import asdict as _asdict

        from clipmaker.segment import candidate_clips, select_clips, split_sentences

        report("highlights", 0.5)
        sentences = split_sentences(result["transcript"]["segments"])
        result["sentences"] = [_asdict(x) for x in sentences]
        from clipmaker.highlights import rescore

        candidates = candidate_clips(sentences)
        parts = rescore(candidates, sentences)
        from clipmaker.llm_scoring import blend_scores, scorer_from_env

        for cid, llm in blend_scores(candidates, scorer_from_env()).items():
            parts[cid]["llm"] = round(llm, 3)
        for n, c in enumerate(select_clips(candidates, max_clips=max_clips), 1):
            result["clips"].append({
                "id": f"clip_{n:02d}", "start": round(c.start, 3), "end": round(c.end, 3),
                "duration": round(c.duration, 3), "text": c.text, "score": c.score,
                "score_parts": parts[id(c)],
            })
    if info.has_video and not video_source:
        # D-013 order: clips are chosen from the audio/transcript alone, so scene detection
        # only decodes the chosen ranges (+ padding). Without clips (no speech) the whole
        # video is scanned, so scene cuts are still reported.
        from clipmaker.scenes import detect_scene_cuts, detect_scene_cuts_in

        t0 = time.perf_counter()
        report("scenes", 0.52)
        spans = [(c["start"], c["end"]) for c in result["clips"]]
        result["scene_cuts"] = detect_scene_cuts_in(input_path, spans, info.duration) if spans \
            else detect_scene_cuts(input_path)
        result["scene_cuts_scope"] = "clips" if spans else "full"
        result["timings"]["scenes_seconds"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    if video_source:
        result["scene_cuts"], result["scene_cuts_scope"] = [], "sections"
        result["video_source"] = "sections"
    if render and result["clips"] and (info.has_video or video_source):
        from clipmaker.captions import caption_keywords, clip_words, group_words, write_ass
        from clipmaker.keywords import hook_title as make_title
        from clipmaker.keywords import PUNCH_IN_SCALE, punch_ins, zoom_expression
        from clipmaker.render import center_crop, render_clip
        from clipmaker.trim import kept_ranges, output_duration, remap_words

        from clipmaker.reframe import FaceDetector, plan_reframe
        from clipmaker.scenes import detect_scene_cuts

        cuts = result["scene_cuts"] or []
        all_words = [w for seg in result["transcript"]["segments"] for w in seg["words"]]
        local = threading.local()  # one face detector per worker: cv2 detectors aren't thread-safe

        def render_one(clip: dict) -> None:
            if reframe and not hasattr(local, "detector"):
                local.detector = FaceDetector()
            detector = getattr(local, "detector", None)
            name = f"{clip['id']}.mp4"
            t_fetch = time.perf_counter()
            if video_source:  # sections mode: this clip's video only, starting at `off`
                src, off = video_source(clip)
                src, off = Path(src), float(off)
                vinfo = probe(src)
                clip_cuts = [round(t + off, 3) for t in detect_scene_cuts(src)]
                result["scene_cuts"].extend(clip_cuts)  # list.extend is atomic under the GIL
                clip["source_offset"] = round(off, 3)
            else:
                src, off, vinfo, clip_cuts = input_path, 0.0, info, cuts
            fetch_seconds = time.perf_counter() - t_fetch
            crop = center_crop(vinfo.width, vinfo.height)
            ranges = kept_ranges(result["transcript"]["segments"], clip["start"], clip["end"]) \
                if remove_dead_air else [[clip["start"], clip["end"]]]
            subs = None
            zoom_expr = None
            if captions:
                words = remap_words(clip_words(all_words, clip["start"], clip["end"]), ranges)
                caps = group_words(words, 0.0)
                title = make_title(clip["text"]) if hook_title else ""
                subs = write_ass(caps, out_dir / f"{clip['id']}.ass", style=caption_style,
                                 keywords=keywords, emoji=emoji, title=title or None,
                                 progress_duration=output_duration(ranges) if progress_bar else None)
                clip["title"] = title
                clip["progress_bar"] = progress_bar
                windows = punch_ins(caps) if zoom else []
                zoom_expr = zoom_expression(windows)
                clip["punch_ins"] = [list(w) for w in windows]
                clip["punch_in_scale"] = PUNCH_IN_SCALE
                clip["captions_file"] = subs.name
                clip["caption_style"] = caption_style
                clip["caption_keywords"] = caption_keywords(caps) if (keywords or emoji) else []
            t_clip = time.perf_counter()
            s0, s1 = clip["start"] - off, clip["end"] - off  # times inside `src`
            plan = plan_reframe(src, s0, s1, vinfo.width, vinfo.height, crop,
                                [c - off for c in clip_cuts], detector) if reframe else None
            t_render = time.perf_counter()
            render_clip(src, out_dir / name, s0, s1,
                        plan.crop if plan else crop, subtitles=subs,
                        ranges=[[a - off, b - off] for a, b in ranges],
                        xy=plan.filter_xy(s0) if plan else None,
                        zoom=zoom_expr, fps=vinfo.fps)
            clip["timings"] = {"reframe_seconds": round(t_render - t_clip, 3),
                               "encode_seconds": round(time.perf_counter() - t_render, 3)}
            if video_source:
                clip["timings"]["fetch_seconds"] = round(fetch_seconds, 3)
            clip["output_duration"] = round(output_duration(ranges), 3)
            clip["file"] = name
            clip["kept_ranges"] = ranges
            track = plan.track() if plan else [[s0, crop.x, crop.y, crop.w, crop.h]]
            clip["crop_track"] = [[round(row[0] + off, 3), *row[1:]] for row in track]
            clip["reframe"] = f"follow-{plan.axis}:{plan.source}" if plan and plan.axis else "static"

        # Clips render in parallel (M5.5): ffmpeg's filter chain is mostly single-threaded,
        # so two clips at once use the cores better than one. Progress is reported from
        # this thread only, as clips finish.
        clips = result["clips"]
        workers = render_workers(len(clips))
        result["render_workers"] = workers
        report(f"render 0/{len(clips)}", 0.55)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(render_one, c): c for c in clips}
            for done, fut in enumerate(as_completed(futures), 1):
                fut.result()
                if on_clip:
                    on_clip(futures[fut])
                report(f"render {done}/{len(clips)}", 0.55 + 0.43 * done / len(clips))
    if video_source and result["scene_cuts"]:
        result["scene_cuts"] = sorted(set(result["scene_cuts"]))
    result["timings"]["render_seconds"] = round(time.perf_counter() - t0, 3)
    result["timings"]["total_seconds"] = round(time.perf_counter() - started, 3)
    (out_dir / "clips.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
