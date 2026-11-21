"""End-to-end pipeline: input video -> clips + clips.json.

Stages so far: probe, scene-cut detection, transcribe, sentence segmentation + clip selection,
dead-air removal, frame-accurate cut with a speaker-following 9:16 crop and burned-in captions.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from clipmaker.media import probe

CLIPS_SCHEMA_VERSION = 1


def run_pipeline(input_path: str | Path, out_dir: str | Path, language: str | None = None,
                 transcribe: bool = True, max_clips: int = 5,
                 render: bool = True, captions: bool = True,
                 remove_dead_air: bool = True, reframe: bool = True,
                 caption_style: str = "bold", keywords: bool = True, emoji: bool = False,
                 hook_title: bool = True, progress_bar: bool = True, zoom: bool = True,
                 progress: Callable[[str, float], None] | None = None) -> dict:
    """`progress(stage, fraction)` is called as the stages advance (fraction 0..1)."""
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
    if info.has_video:
        from clipmaker.scenes import detect_scene_cuts

        t0 = time.perf_counter()
        report("scenes", 0.02)
        result["scene_cuts"] = detect_scene_cuts(input_path)
        result["timings"]["scenes_seconds"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    info_ok = info.has_audio and transcribe
    if info_ok:
        from clipmaker.transcribe import transcribe as run_asr

        report("transcribe", 0.08)

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
    t0 = time.perf_counter()
    if render and result["clips"] and info.has_video:
        from clipmaker.captions import caption_keywords, clip_words, group_words, write_ass
        from clipmaker.keywords import hook_title as make_title
        from clipmaker.keywords import PUNCH_IN_SCALE, punch_ins, zoom_expression
        from clipmaker.render import center_crop, render_clip
        from clipmaker.trim import kept_ranges, output_duration, remap_words

        from clipmaker.reframe import FaceDetector, plan_reframe

        crop = center_crop(info.width, info.height)
        detector = FaceDetector() if reframe else None
        cuts = result["scene_cuts"] or []
        all_words = [w for seg in result["transcript"]["segments"] for w in seg["words"]]
        for n_clip, clip in enumerate(result["clips"]):
            report(f"render {n_clip + 1}/{len(result['clips'])}", 0.55 + 0.43 * n_clip / len(result["clips"]))
            name = f"{clip['id']}.mp4"
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
            plan = plan_reframe(input_path, clip["start"], clip["end"], info.width, info.height, crop,
                                cuts, detector) if reframe else None
            render_clip(input_path, out_dir / name, clip["start"], clip["end"],
                        plan.crop if plan else crop, subtitles=subs, ranges=ranges,
                        xy=plan.filter_xy(clip["start"]) if plan else None,
                        zoom=zoom_expr, fps=info.fps)
            clip["output_duration"] = round(output_duration(ranges), 3)
            clip["file"] = name
            clip["kept_ranges"] = ranges
            clip["crop_track"] = plan.track() if plan else [[clip["start"], crop.x, crop.y, crop.w, crop.h]]
            clip["reframe"] = f"follow-{plan.axis}:{plan.source}" if plan and plan.axis else "static"
    result["timings"]["render_seconds"] = round(time.perf_counter() - t0, 3)
    result["timings"]["total_seconds"] = round(time.perf_counter() - started, 3)
    (out_dir / "clips.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
