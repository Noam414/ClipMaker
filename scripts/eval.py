#!/usr/bin/env python3
"""Run the ClipMaker pipeline on every fixture and score it against ground truth.

Usage:
    python3 scripts/eval.py                 # all fixtures
    python3 scripts/eval.py --only podcast_landscape

Writes eval/results/latest.json, appends a line to eval/results/history.jsonl,
keeps rendered clips in eval/results/clips/ and contact sheets in
eval/results/frames/ (both git-ignored).

Metrics are null when the pipeline does not support them yet.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from clipmaker import __version__  # noqa: E402
from clipmaker.metrics import aggregate, score_fixture  # noqa: E402
from clipmaker.pipeline import run_pipeline  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "videos"
RESULTS = ROOT / "eval" / "results"


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", help="fixture name (repeatable)")
    parser.add_argument("--no-history", action="store_true", help="don't append to history.jsonl")
    args = parser.parse_args(argv)

    manifest_path = FIXTURES / "manifest.json"
    if not manifest_path.is_file():
        print("fixtures missing: run python3 scripts/make_test_videos.py", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    per_fixture: dict[str, dict] = {}
    started = time.perf_counter()
    for name, gt in manifest["fixtures"].items():
        if args.only and name not in args.only:
            continue
        out_dir = RESULTS / "clips" / name
        t0 = time.perf_counter()
        error = None
        try:
            result = run_pipeline(FIXTURES / gt["file"], out_dir)
        except Exception as exc:  # keep evaluating other fixtures
            result, error = None, f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - t0
        per_fixture[name] = score_fixture(gt, result, out_dir, elapsed, RESULTS / "frames" / name)
        if error:
            per_fixture[name]["error"] = error
        print(f"{name}: {json.dumps(per_fixture[name]['metrics'])}", flush=True)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "version": __version__,
        "eval_seconds": round(time.perf_counter() - started, 1),
        "summary": aggregate(per_fixture),
        "fixtures": per_fixture,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.no_history:
        line = {k: report[k] for k in ("timestamp", "git_sha", "version", "eval_seconds", "summary")}
        with open(RESULTS / "history.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    print("summary:", json.dumps(report["summary"]))
    errors = [n for n, r in per_fixture.items() if r.get("error")]
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
