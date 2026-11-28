"""Edit a rendered clip (M5): new caption text, a new hook title, trimmed start/end, then
re-render that one clip from the project's stored transcript and video (D-031).

Edited caption text keeps the transcript's word timings wherever the words still match
(difflib alignment); changed or inserted words share the time of the words they replace.
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

MIN_CLIP_S = 3.0
MAX_CLIP_S = 90.0


class EditError(ValueError):
    """A user-facing problem with the requested edit."""


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


def _spread(tokens: list[str], t0: float, t1: float) -> list[dict]:
    """Words over [t0, t1], each getting time in proportion to its length."""
    if not tokens:
        return []
    t1 = max(t1, t0 + 0.05 * len(tokens))
    weights = [max(1, len(t)) for t in tokens]
    total, acc, out = sum(weights), t0, []
    for tok, w in zip(tokens, weights):
        nxt = acc + (t1 - t0) * w / total
        out.append({"word": tok, "start": round(acc, 3), "end": round(nxt, 3)})
        acc = nxt
    return out


def retime_words(old: list[dict], new_text: str) -> list[dict]:
    """Caption words for `new_text`, timed against the clip's original `old` words."""
    tokens = new_text.split()
    if not old:
        raise EditError("This clip has no transcript words to edit.")
    if not tokens:
        raise EditError("The caption text is empty.")
    a, b = [_norm(w["word"]) for w in old], [_norm(t) for t in tokens]
    out: list[dict] = []
    pending: list[str] = []  # words inserted before the first old word
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        mark = len(out)
        if op == "equal":  # keep the timing; take the new spelling/punctuation
            out += [{**old[i], "word": tokens[j]} for i, j in zip(range(i1, i2), range(j1, j2))]
        elif op == "replace":
            out += _spread(tokens[j1:j2], old[i1]["start"], old[i2 - 1]["end"])
        elif op == "insert":
            if out:  # share the previous word's time (and any gap before the next word)
                prev = out.pop()
                nxt = old[i1]["start"] if i1 < len(old) else prev["end"]
                out += _spread([prev["word"], *tokens[j1:j2]], prev["start"], max(prev["end"], nxt))
            else:
                pending = tokens[j1:j2]
            continue
        if pending and len(out) > mark:  # leading inserts share the first word's time
            first = out[mark]
            out[mark:mark + 1] = _spread([*pending, first["word"]], first["start"], first["end"])
            pending = []
        # "delete": the words are simply gone from the captions (the audio is unchanged)
    return out


def check_trim(start: float, end: float, lo: float, hi: float) -> tuple[float, float]:
    """Validate a trimmed range against the available video [lo, hi]."""
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        raise EditError("Start and end must be numbers (seconds).") from None
    if start < lo - 1e-3 or end > hi + 1e-3:
        raise EditError(f"Start and end must stay between {lo:.1f} s and {hi:.1f} s "
                        "(the video that is available for this clip).")
    if end - start < MIN_CLIP_S:
        raise EditError(f"A clip must be at least {MIN_CLIP_S:g} seconds long.")
    if end - start > MAX_CLIP_S:
        raise EditError(f"A clip can be at most {MAX_CLIP_S:g} seconds long.")
    return round(max(start, lo), 3), round(min(end, hi), 3)


def video_for_clip(pdir: Path, clip: dict, source_file: str) -> tuple[Path, float, float, float]:
    """(file, its start time in the source, first and last usable source time) for a clip.
    Links in sections mode (YouTube/Twitch/HLS) only have the clip's padded section on disk,
    so a trim must stay inside it; uploads and direct links have the whole video."""
    from clipmaker.media import probe

    pdir = Path(pdir)
    if "source_offset" in clip:
        src = pdir / "sections" / f"{clip['id']}_source.mp4"
        off = float(clip["source_offset"])
    else:
        src, off = pdir / source_file, 0.0
    if not src.is_file():
        raise EditError("The video for this clip is no longer on the server, so it can't be re-rendered.")
    info = probe(src)
    return src, off, off, off + (info.duration or 0.0)


def edit_bounds(pdir: Path, clip: dict, source_file: str) -> tuple[float, float]:
    _, _, lo, hi = video_for_clip(pdir, clip, source_file)
    return round(lo, 3), round(hi, 3)


def _load_result(out_dir: Path) -> dict:
    path = Path(out_dir) / "clips.json"
    if not path.is_file():
        raise EditError("This project's transcript is missing, so the clip can't be re-rendered.")
    return json.loads(path.read_text(encoding="utf-8"))


def rerender_clip(pdir: Path, source_file: str, clip: dict, options: dict, *,
                  start: float | None = None, end: float | None = None,
                  text: str | None = None, title: str | None = None) -> dict:
    """Re-render one clip with the edits; returns its new clips.json entry. The new file gets
    a new name (`clip_01_e2.mp4`) so browsers don't show a cached old version."""
    from clipmaker.media import probe
    from clipmaker.pipeline import render_clip_entry
    from clipmaker.reframe import FaceDetector
    from clipmaker.scenes import detect_scene_cuts

    pdir = Path(pdir)
    out_dir = pdir / "clips"
    result = _load_result(out_dir)
    segments = (result.get("transcript") or {}).get("segments") or []
    all_words = [w for seg in segments for w in seg["words"]]
    src, off, lo, hi = video_for_clip(pdir, clip, source_file)
    start = clip["start"] if start is None else start
    end = clip["end"] if end is None else end
    start, end = check_trim(start, end, lo, hi)

    from clipmaker.captions import clip_words

    edited = dict(clip.get("edit") or {})
    words = None
    if text is not None:
        # retime against the transcript's words in the clip as it is now (its shown text)
        base = clip_words(all_words, clip["start"], clip["end"])
        words = retime_words(base, text)
        edited.update(text=text.strip(), words=words, span=[clip["start"], clip["end"]])
    elif edited.get("words"):
        words = edited["words"]
    if words is not None:
        # transcript words outside the edited span still caption a widened trim
        a, b = edited["span"]
        words = [w for w in all_words if w["end"] <= a + 0.05] + words + \
                [w for w in all_words if w["start"] >= b - 0.05]
    if title is not None:
        edited["title"] = title.strip()[:120]
    n = int(edited.get("version", 1)) + 1
    edited["version"] = n
    new = {k: v for k, v in clip.items() if k not in ("timings",)}
    new.update(start=start, end=end, duration=round(end - start, 3), edit=edited)
    if words is not None or start != clip["start"] or end != clip["end"]:
        source_words = words if words is not None else all_words
        new["text"] = " ".join(w["word"] for w in clip_words(source_words, start, end))
    vinfo = probe(src)
    cuts = [round(t + off, 3) for t in detect_scene_cuts(src)] if options.get("reframe", True) else []
    detector = FaceDetector() if options.get("reframe", True) else None
    render_clip_entry(new, src, off, vinfo, all_words, segments, cuts, out_dir, detector, options,
                      words=words, title=edited.get("title", clip.get("title")), name=f"{clip['id']}_e{n}.mp4")
    old_file = clip.get("file")
    _save_result(out_dir, result, new)
    if old_file and old_file != new["file"]:
        for stale in (out_dir / old_file, out_dir / f"{Path(old_file).stem}.ass"):
            stale.unlink(missing_ok=True)
    return new


def _save_result(out_dir: Path, result: dict, clip: dict) -> None:
    result["clips"] = [clip if c.get("id") == clip["id"] else c for c in result.get("clips", [])]
    path = Path(out_dir) / "clips.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
