"""Free-tier guardrails, part 1 (M7, D-029): delete old projects and keep disk space free.

- `CLIPMAKER_RETENTION_HOURS` (0 = keep forever, the default for local use; the Oracle
  deploy kit sets 24): projects older than this, with no queued or running job, are deleted:
  their files and their database rows.
- `CLIPMAKER_MIN_FREE_GB` (0 = off; the kit sets 10): when the data disk has less free space,
  the oldest finished projects are deleted early, and new uploads/links are refused with
  "the server is full" if that still isn't enough.

A `Janitor` thread in the web process runs both every few minutes.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, exists, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from clipmaker.jobs import project_dir
from clipmaker.store import Clip, Job, Project

log = logging.getLogger(__name__)
GB = 1024 ** 3
ACTIVE = ("queued", "running")


def retention_hours() -> float:
    return float(os.environ.get("CLIPMAKER_RETENTION_HOURS", "0") or 0)


def min_free_bytes() -> int:
    return int(float(os.environ.get("CLIPMAKER_MIN_FREE_GB", "0") or 0) * GB)


def free_bytes(path: Path) -> int:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free


def _idle_projects(s: Session, older_than: datetime | None = None) -> list[Project]:
    """Projects without a queued or running job, oldest first."""
    busy = exists().where(Job.project_id == Project.id, Job.status.in_(ACTIVE))
    q = select(Project).where(~busy).order_by(Project.created_at, Project.id)
    if older_than is not None:
        q = q.where(Project.created_at < older_than)
    return list(s.scalars(q))


def delete_project(engine: Engine, data_dir: Path, project_id: int) -> None:
    with Session(engine) as s:
        s.execute(delete(Clip).where(Clip.project_id == project_id))
        s.execute(delete(Job).where(Job.project_id == project_id))
        s.execute(delete(Project).where(Project.id == project_id))
        s.commit()
    shutil.rmtree(project_dir(data_dir, project_id), ignore_errors=True)


def expire_old(engine: Engine, data_dir: Path, hours: float, now: datetime | None = None) -> list[int]:
    """Delete idle projects created more than `hours` ago; return their ids."""
    if hours <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    # created_at is stored naive UTC by SQLite
    cutoff = (now - timedelta(hours=hours)).replace(tzinfo=None)
    with Session(engine) as s:
        ids = [p.id for p in _idle_projects(s, cutoff)]
    for pid in ids:
        delete_project(engine, data_dir, pid)
    if ids:
        log.info("deleted %d project(s) older than %g h: %s", len(ids), hours, ids)
    return ids


def make_room(engine: Engine, data_dir: Path, need_free: int,
              free: Callable[[Path], int] | None = None) -> list[int]:
    """Delete the oldest idle projects until `need_free` bytes are free (or none are left)."""
    free = free or free_bytes
    if need_free <= 0 or free(data_dir) >= need_free:
        return []
    with Session(engine) as s:
        candidates = [p.id for p in _idle_projects(s)]
    deleted = []
    for pid in candidates:
        delete_project(engine, data_dir, pid)
        deleted.append(pid)
        if free(data_dir) >= need_free:
            break
    log.warning("low disk space: deleted %d oldest project(s) early: %s", len(deleted), deleted)
    return deleted


def has_room(engine: Engine, data_dir: Path, free: Callable[[Path], int] | None = None) -> bool:
    """True when a new job may start: enough free disk, after making room if needed."""
    free = free or free_bytes
    need = min_free_bytes()
    if need <= 0:
        return True
    make_room(engine, data_dir, need, free)
    return free(data_dir) >= need


class Janitor:
    """Background thread running `expire_old` and `make_room` every `interval` seconds."""

    def __init__(self, engine: Engine, data_dir: Path, interval: float = 600.0):
        self.engine, self.data_dir, self.interval = engine, Path(data_dir), interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def enabled() -> bool:
        return retention_hours() > 0 or min_free_bytes() > 0

    def run_once(self) -> None:
        expire_old(self.engine, self.data_dir, retention_hours())
        make_room(self.engine, self.data_dir, min_free_bytes())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # never let the thread die
                log.exception("janitor error")
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="clipmaker-janitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
