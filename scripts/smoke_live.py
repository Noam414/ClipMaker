#!/usr/bin/env python3
"""Smoke test against a running ClipMaker site (M7): health, upload a video, wait for the clips,
download one and check it, optionally edit a clip and wait for the re-render.

    python3 scripts/smoke_live.py https://<ip-with-dashes>.sslip.io
    python3 scripts/smoke_live.py http://127.0.0.1:8000 --video my.mp4 --edit

Standard library only (ffprobe is used when present), so it runs anywhere, including the
server itself. Exit code 0 = all good; 1 = a check failed (the reason is printed).
Each run counts against the site's daily limit per visitor (the kit sets 5 a day).
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VIDEO = ROOT / "tests" / "fixtures" / "videos" / "portrait_short.mp4"


class SmokeError(Exception):
    pass


class Site:
    def __init__(self, base: str, timeout: float = 60.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        # a cookie jar keeps the visitor cookie, so a private site (D-030) shows us our project
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path: str, data: bytes | None = None, headers: dict | None = None,
                method: str | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {}, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise SmokeError(f"{path}: cannot reach the site ({exc})") from None

    def json(self, path: str, body: dict | None = None, expect: tuple[int, ...] = (200,)) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        status, raw = self.request(path, data, {"Content-Type": "application/json"} if data else None)
        return _decode(path, status, raw, expect)

    def upload(self, video: Path) -> dict:
        boundary = uuid.uuid4().hex
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{video.name}\"\r\nContent-Type: video/mp4\r\n\r\n").encode()
        body = head + video.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        status, raw = self.request("/api/projects", body,
                                   {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return _decode("/api/projects", status, raw, (201,))


def _decode(path: str, status: int, raw: bytes, expect: tuple[int, ...]) -> dict:
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = {"detail": raw[:200].decode(errors="replace")}
    if status not in expect:
        raise SmokeError(f"{path}: HTTP {status}: {body.get('detail', body)}")
    return body


def wait_job(site: Site, job_id: int, timeout: float, say) -> dict:
    deadline, last = time.monotonic() + timeout, None
    while True:
        job = site.json(f"/api/jobs/{job_id}")
        line = f"{job['status']} {job['stage']} {job['progress'] * 100:.0f}%"
        if job.get("queue_ahead"):
            line += f" ({job['queue_ahead']} ahead in the queue)"
        if line != last:
            say(f"  job {job_id}: {line}")
            last = line
        if job["status"] == "done":
            return job
        if job["status"] == "failed":
            raise SmokeError(f"job {job_id} failed: {(job.get('error') or '').splitlines()[0:1]}")
        if time.monotonic() > deadline:
            raise SmokeError(f"job {job_id} not done after {timeout:.0f} s (last: {line})")
        time.sleep(2)


def check_clip(site: Site, url: str, say) -> None:
    status, data = site.request(url)
    if status != 200 or len(data) < 10_000:
        raise SmokeError(f"{url}: HTTP {status}, {len(data)} bytes")
    if not shutil.which("ffprobe"):
        say(f"  {url}: {len(data)} bytes (ffprobe not found, format not checked)")
        return
    with tempfile.NamedTemporaryFile(suffix=".mp4") as fh:
        fh.write(data)
        fh.flush()
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                              "-of", "json", fh.name], capture_output=True, text=True)
    streams = json.loads(out.stdout or "{}").get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    if out.returncode or not video:
        raise SmokeError(f"{url}: not a playable video ({out.stderr.strip()[:200]})")
    w, h = video[0].get("width"), video[0].get("height")
    if (w, h) != (1080, 1920):
        raise SmokeError(f"{url}: {w}x{h}, expected 1080x1920")
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise SmokeError(f"{url}: no audio track")
    say(f"  {url}: {len(data) // 1024} KB, {w}x{h}, audio ok")


def run(base: str, video: Path, timeout: float = 600, edit: bool = False, say=print) -> dict:
    """Run every check; returns a summary dict, raises SmokeError on the first failure."""
    t0 = time.monotonic()
    site = Site(base)
    health = site.json("/api/health")
    if health.get("status") != "ok" or health.get("database") is not True:
        raise SmokeError(f"/api/health: {health}")
    say(f"health ok, version {health.get('version')}")
    status, page = site.request("/")
    if status != 200 or b"ClipMaker" not in page:
        raise SmokeError(f"home page: HTTP {status}")
    if not video.is_file():
        raise SmokeError(f"{video}: no such file (run python3 scripts/make_test_videos.py or pass --video)")
    up = site.upload(video)
    say(f"uploaded {video.name} ({video.stat().st_size // 1024} KB): project {up['project_id']}")
    wait_job(site, up["job_id"], timeout, say)
    project = site.json(f"/api/projects/{up['project_id']}")
    clips = [c for c in project["clips"] if c.get("url")]
    if not clips:
        raise SmokeError("the job finished without clips")
    say(f"{len(clips)} clip(s); best score {max(c['score'] for c in clips):.2f}")
    check_clip(site, clips[0]["url"], say)
    status, _ = site.request(f"/projects/{up['project_id']}")
    if status != 200:
        raise SmokeError(f"gallery page: HTTP {status}")
    summary = {"project_id": up["project_id"], "clips": len(clips)}
    if edit:
        c = clips[0]
        job = site.json(f"/api/clips/{c['id']}/edit", {"title": "Smoke test"}, expect=(202,))
        wait_job(site, job["job_id"], timeout, say)
        after = next(x for x in site.json(f"/api/projects/{up['project_id']}")["clips"] if x["id"] == c["id"])
        if after["url"] == c["url"] or after["title"] != "Smoke test":
            raise SmokeError(f"edit did not produce a new clip: {after}")
        check_clip(site, after["url"], say)
        summary["edited"] = True
    summary["seconds"] = round(time.monotonic() - t0, 1)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url", help="the site, e.g. https://1-2-3-4.sslip.io")
    ap.add_argument("--video", type=Path, default=DEFAULT_VIDEO, help="video to upload (default: portrait_short fixture)")
    ap.add_argument("--timeout", type=float, default=600, help="seconds to wait for each job (default 600)")
    ap.add_argument("--edit", action="store_true", help="also edit the best clip's title and wait for the re-render")
    args = ap.parse_args(argv)
    try:
        summary = run(args.url, args.video, args.timeout, args.edit)
    except SmokeError as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"SMOKE OK: {json.dumps(summary)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
