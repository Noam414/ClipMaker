"""DB-backed job queue (D-001, D-012): a `jobs` table polled by a worker thread.

No Redis or Celery: one web process runs one worker thread. `claim_next` uses a guarded
UPDATE (status='queued' -> 'running'), so several workers on one database never take the
same job.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from clipmaker.store import Clip, Job, Project, session_factory, utcnow

log = logging.getLogger("clipmaker.jobs")

# runner(input_path, out_dir, progress=callback, **options) -> clips.json dict
Runner = Callable[..., dict]


def project_dir(data_dir: Path, project_id: int) -> Path:
    return Path(data_dir) / "projects" / str(project_id)


def enqueue(engine: Engine, project_id: int, options: dict | None = None) -> Job:
    with session_factory(engine)() as s:
        job = Job(project_id=project_id, options=options or {})
        s.add(job)
        s.commit()
        return job


def requeue_stale(engine: Engine) -> int:
    """Jobs left 'running' by a process that died go back to the queue (call at startup)."""
    with engine.begin() as conn:
        res = conn.execute(update(Job).where(Job.status == "running")
                           .values(status="queued", stage="queued", progress=0.0))
        return res.rowcount or 0


def claim_next(engine: Engine) -> int | None:
    with session_factory(engine)() as s:
        for job_id in s.scalars(select(Job.id).where(Job.status == "queued").order_by(Job.id).limit(5)):
            res = s.execute(update(Job).where(Job.id == job_id, Job.status == "queued")
                            .values(status="running", stage="starting", progress=0.0, started_at=utcnow()))
            s.commit()
            if res.rowcount == 1:
                return job_id
    return None


def _set_progress(engine: Engine, job_id: int, stage: str, frac: float) -> None:
    with engine.begin() as conn:
        conn.execute(update(Job).where(Job.id == job_id)
                     .values(stage=stage[:64], progress=max(0.0, min(1.0, float(frac)))))


def _clip_row(project_id: int, job_id: int, c: dict) -> Clip:
    return Clip(project_id=project_id, job_id=job_id, key=c["id"], start=c["start"], end=c["end"],
                score=float(c.get("score") or 0.0), title=c.get("title") or "", text=c.get("text") or "",
                file=f"clips/{c['file']}" if c.get("file") else None, data=c)


def check_upload_length(source: Path) -> None:
    """Free-tier guardrail (D-029): `CLIPMAKER_MAX_UPLOAD_MINUTES` (0 = no limit, the kit sets
    60, like the link limit) fails an uploaded video that is too long, before any work."""
    import os

    limit = float(os.environ.get("CLIPMAKER_MAX_UPLOAD_MINUTES", "0") or 0)
    if limit <= 0:
        return
    from clipmaker.fetch import FetchError
    from clipmaker.media import probe

    try:
        duration = probe(source).duration or 0.0
    except Exception:  # unreadable files fail in the pipeline with their own message
        return
    if duration > limit * 60:
        raise FetchError(f"This video is {duration / 60:.0f} minutes long; the limit here is "
                         f"{limit:g} minutes. Trim it or upload a shorter part.")


def is_edit_job(job: Job) -> bool:
    return bool((job.options or {}).get("edit"))


def main_job(project: Project) -> Job | None:
    """The project's latest clip-making job; edit jobs (re-renders of one clip) don't count."""
    jobs = [j for j in project.jobs if not is_edit_job(j)]
    return jobs[-1] if jobs else None


def _fail(engine: Engine, job_id: int, exc: Exception) -> str:
    from clipmaker.edit import EditError
    from clipmaker.fetch import FetchError

    log.exception("job %s failed", job_id)
    with session_factory(engine)() as s:
        job = s.get(Job, job_id)
        job.status, job.stage, job.finished_at = "failed", "failed", utcnow()
        user_safe = isinstance(exc, (FetchError, EditError))
        prefix = "" if user_safe else f"{type(exc).__name__}: "
        job.error = f"{prefix}{exc}"[:2000] + "\n" + traceback.format_exc()[-2000:]
        s.commit()
    return "failed"


def run_edit_job(engine: Engine, data_dir: Path, job_id: int) -> str:
    """Re-render one edited clip (M5, D-031) and update its row in place."""
    from clipmaker.edit import rerender_clip

    Session = session_factory(engine)
    with Session() as s:
        job = s.get(Job, job_id)
        project = s.get(Project, job.project_id)
        edit = dict(job.options["edit"])
        clip = s.get(Clip, edit["clip_row"])
        origin = s.get(Job, clip.job_id)
        pdir, source_file = project_dir(data_dir, project.id), project.source_file
        data, options = dict(clip.data or {}), dict(origin.options or {})
    try:
        _set_progress(engine, job_id, "re-render", 0.1)
        new = rerender_clip(pdir, source_file, data, options, start=edit.get("start"),
                            end=edit.get("end"), text=edit.get("text"), title=edit.get("title"))
    except Exception as exc:
        return _fail(engine, job_id, exc)
    with Session() as s:
        clip = s.get(Clip, edit["clip_row"])
        clip.start, clip.end, clip.text = new["start"], new["end"], new.get("text") or ""
        clip.title, clip.file, clip.data = new.get("title") or "", f"clips/{new['file']}", new
        job = s.get(Job, job_id)
        job.status, job.stage, job.progress, job.finished_at = "done", "done", 1.0, utcnow()
        s.commit()
    return "done"


def run_job(engine: Engine, data_dir: Path, job_id: int, runner: Runner) -> str:
    """Run one claimed job; store its clips. Returns the final status."""
    Session = session_factory(engine)
    with Session() as s:
        job = s.get(Job, job_id)
        if is_edit_job(job):
            return run_edit_job(engine, data_dir, job_id)
        project = s.get(Project, job.project_id)
        pdir = project_dir(data_dir, project.id)
        source, options, project_id = pdir / project.source_file, dict(job.options or {}), project.id
    out_dir = pdir / "clips"
    stored: set[str] = set()

    def store_clip(c: dict) -> None:
        # Clips are stored as soon as they're rendered, so the gallery fills in one by one.
        with Session() as s:
            s.add(_clip_row(project_id, job_id, c))
            s.commit()
        stored.add(c["id"])

    try:
        url = options.pop("source_url", None)
        options.pop("rights_confirmed", None)  # recorded on the job; not a pipeline option
        from clipmaker.hls import is_hls
        from clipmaker.platforms import platform_of

        if url and platform_of(url):  # YouTube/Twitch: audio first, then only the clips' video
            from clipmaker.media import probe
            from clipmaker.platforms import fetch_audio, section_source

            _set_progress(engine, job_id, "download audio", 0.0)
            source = fetch_audio(url, pdir / "download")
            options["video_source"] = section_source(url, pdir / "sections", probe(source).duration)
        elif url and is_hls(url):  # HLS: audio first, then only the clips' segments (D-024)
            from clipmaker import hls

            _set_progress(engine, job_id, "download audio", 0.0)
            source = hls.fetch_audio(url, source, progress=lambda f: _set_progress(
                engine, job_id, "download audio", f))
            options["video_source"] = hls.section_source(url, pdir / "sections")
        elif url:  # a pasted direct link: download it inside the job, with progress (M5.5)
            from clipmaker.fetch import download

            _set_progress(engine, job_id, "download", 0.0)
            download(url, source, progress=lambda f: _set_progress(engine, job_id, "download", f))
        else:
            check_upload_length(source)
        result = runner(source, out_dir, progress=lambda st, f: _set_progress(engine, job_id, st, f),
                        on_clip=store_clip, **options)
    except Exception as exc:  # the job fails, the worker keeps going
        return _fail(engine, job_id, exc)
    with Session() as s:
        for c in result.get("clips", []):
            if c["id"] not in stored:
                s.add(_clip_row(project_id, job_id, c))
        job = s.get(Job, job_id)
        job.status, job.stage, job.progress, job.finished_at = "done", "done", 1.0, utcnow()
        s.commit()
    return "done"


def default_runner(input_path: Path, out_dir: Path, progress=None, **options) -> dict:
    from clipmaker.pipeline import run_pipeline

    return run_pipeline(input_path, out_dir, progress=progress, **options)


class Worker:
    """Background thread that polls the queue."""

    def __init__(self, engine: Engine, data_dir: Path, runner: Runner = default_runner, poll: float = 1.0):
        self.engine, self.data_dir, self.runner, self.poll = engine, Path(data_dir), runner, poll
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> str | None:
        job_id = claim_next(self.engine)
        if job_id is None:
            return None
        return run_job(self.engine, self.data_dir, job_id, self.runner)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.run_once() is None:
                    self._stop.wait(self.poll)
            except Exception:  # never let the thread die on a DB hiccup
                log.exception("worker loop error")
                time.sleep(self.poll)

    def start(self) -> None:
        requeue_stale(self.engine)
        self._thread = threading.Thread(target=self._loop, name="clipmaker-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
