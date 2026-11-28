"""Free-tier guardrails part 1 (D-029): 24 h retention and the free-disk floor."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clipmaker import cleanup
from clipmaker.cleanup import GB, Janitor, expire_old, has_room, make_room
from clipmaker.config import Settings
from clipmaker.db import make_engine
from clipmaker.jobs import project_dir
from clipmaker.store import Clip, Job, Project, init_db, utcnow
from clipmaker.web.app import create_app

NOW = datetime(2026, 11, 27, 12, 0)


def _engine(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(engine)
    return engine


def _project(engine, data_dir, age_h: float, status: str = "done") -> int:
    with Session(engine) as s:
        p = Project(name="v", source_file="source.mp4", created_at=NOW - timedelta(hours=age_h))
        s.add(p)
        s.flush()
        j = Job(project_id=p.id, status=status)
        s.add(j)
        s.flush()
        s.add(Clip(project_id=p.id, job_id=j.id, key="clip_01", start=0, end=20, data={}))
        s.commit()
        pid = p.id
    d = project_dir(data_dir, pid) / "clips"
    d.mkdir(parents=True)
    (d / "clip_01.mp4").write_bytes(b"x" * 10)
    return pid


def _count(engine, model) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(model))


def test_expire_old_deletes_files_and_rows_but_not_active_jobs(tmp_path):
    engine = _engine(tmp_path)
    old = _project(engine, tmp_path, 30)
    old_failed = _project(engine, tmp_path, 25, "failed")
    old_running = _project(engine, tmp_path, 30, "running")
    old_queued = _project(engine, tmp_path, 40, "queued")
    fresh = _project(engine, tmp_path, 2)
    deleted = expire_old(engine, tmp_path, 24, now=NOW)
    assert sorted(deleted) == sorted([old, old_failed])
    assert not project_dir(tmp_path, old).exists()
    for pid in (old_running, old_queued, fresh):
        assert project_dir(tmp_path, pid).exists()
    assert _count(engine, Project) == 3 and _count(engine, Job) == 3 and _count(engine, Clip) == 3
    assert expire_old(engine, tmp_path, 0, now=NOW) == []  # 0 = keep forever


def test_make_room_deletes_oldest_idle_first(tmp_path):
    engine = _engine(tmp_path)
    a = _project(engine, tmp_path, 10)
    busy = _project(engine, tmp_path, 9, "running")
    b = _project(engine, tmp_path, 5)
    c = _project(engine, tmp_path, 1)
    # pretend every deleted project frees 1 GB, starting at 0.5 GB free
    state = {"free": GB // 2}
    orig = cleanup.delete_project

    def free(_):
        return state["free"]

    def counting_delete(engine_, data_dir, pid):
        orig(engine_, data_dir, pid)
        state["free"] += GB

    cleanup.delete_project = counting_delete
    try:
        assert make_room(engine, tmp_path, 2 * GB, free) == [a, b]
        assert project_dir(tmp_path, c).exists() and project_dir(tmp_path, busy).exists()
        assert make_room(engine, tmp_path, 2 * GB, free) == []  # enough now
    finally:
        cleanup.delete_project = orig


def test_has_room(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.delenv("CLIPMAKER_MIN_FREE_GB", raising=False)
    assert has_room(engine, tmp_path, lambda _: 0)  # off by default
    monkeypatch.setenv("CLIPMAKER_MIN_FREE_GB", "10")
    assert not has_room(engine, tmp_path, lambda _: 5 * GB)
    assert has_room(engine, tmp_path, lambda _: 11 * GB)


def test_janitor_enabled_and_run_once(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.delenv("CLIPMAKER_RETENTION_HOURS", raising=False)
    monkeypatch.delenv("CLIPMAKER_MIN_FREE_GB", raising=False)
    assert not Janitor.enabled()
    monkeypatch.setenv("CLIPMAKER_RETENTION_HOURS", "24")
    assert Janitor.enabled()
    with Session(engine) as s:  # real clock: 2 days old vs. just now
        s.add(Project(name="old", source_file="s.mp4", created_at=utcnow() - timedelta(days=2)))
        s.add(Project(name="new", source_file="s.mp4"))
        s.commit()
    Janitor(engine, tmp_path).run_once()
    with Session(engine) as s:
        assert [p.name for p in s.scalars(select(Project))] == ["new"]


def _settings(tmp_path):
    return Settings(database_url=f"sqlite:///{(tmp_path / 'db' / 't.db').as_posix()}",
                    data_dir=tmp_path, cache_dir=tmp_path / "cache", host="127.0.0.1", port=0, worker=False)


def test_upload_and_link_refused_when_disk_is_full(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPMAKER_MIN_FREE_GB", "10")
    monkeypatch.setattr(cleanup, "free_bytes", lambda _: 1 * GB)
    client = TestClient(create_app(_settings(tmp_path)))
    r = client.post("/api/projects", files={"file": ("talk.mp4", b"0" * 100, "video/mp4")})
    assert r.status_code == 503 and "full" in r.json()["detail"]
    r = client.post("/api/projects/link", json={"url": "https://example.com/talk.mp4"})
    assert r.status_code == 503
    monkeypatch.setattr(cleanup, "free_bytes", lambda _: 50 * GB)
    r = client.post("/api/projects", files={"file": ("talk.mp4", b"0" * 100, "video/mp4")})
    assert r.status_code == 201


def test_gallery_says_when_files_are_deleted(tmp_path, monkeypatch):
    client = TestClient(create_app(_settings(tmp_path)))
    pid = client.post("/api/projects", files={"file": ("talk.mp4", b"0" * 100, "video/mp4")}).json()["project_id"]
    engine = client.app.state.engine
    with Session(engine) as s:
        s.get(Job, 1).status = "done"
        s.commit()
    monkeypatch.delenv("CLIPMAKER_RETENTION_HOURS", raising=False)
    assert 'id="expiry"' not in client.get(f"/projects/{pid}").text
    monkeypatch.setenv("CLIPMAKER_RETENTION_HOURS", "24")
    page = client.get(f"/projects/{pid}").text
    assert "deleted 24 h after upload" in page and "(in about 23 h)" in page


def test_upload_length_limit(tmp_path, tiny_video, monkeypatch):
    import pytest

    from clipmaker.fetch import FetchError
    from clipmaker.jobs import check_upload_length

    monkeypatch.delenv("CLIPMAKER_MAX_UPLOAD_MINUTES", raising=False)
    check_upload_length(tiny_video)  # off by default
    monkeypatch.setenv("CLIPMAKER_MAX_UPLOAD_MINUTES", "60")
    check_upload_length(tiny_video)  # 3 s is fine
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"0" * 100)
    check_upload_length(junk)  # unreadable: left to the pipeline's own error
    monkeypatch.setenv("CLIPMAKER_MAX_UPLOAD_MINUTES", "0.02")  # 1.2 s
    with pytest.raises(FetchError, match="the limit here is 0.02 minutes"):
        check_upload_length(tiny_video)


def test_too_long_upload_fails_job_with_readable_message(tmp_path, tiny_video, monkeypatch):
    calls = []
    monkeypatch.setenv("CLIPMAKER_MAX_UPLOAD_MINUTES", "0.02")
    app = create_app(_settings(tmp_path), runner=lambda *a, **k: calls.append(1) or {"clips": []})
    client = TestClient(app)
    job_id = client.post("/api/projects", files={"file": ("talk.mp4", tiny_video.read_bytes(), "video/mp4")}
                         ).json()["job_id"]
    assert app.state.worker.run_once() == "failed"
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["error"].startswith("This video is 0 minutes long; the limit here is 0.02 minutes") and not calls
