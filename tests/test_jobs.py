import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from clipmaker.config import Settings
from clipmaker.db import make_engine
from clipmaker.jobs import claim_next, enqueue, requeue_stale
from clipmaker.store import Job, Project, init_db, session_factory
from clipmaker.web.app import create_app


def _settings(tmp_path, worker=False):
    return Settings(database_url=f"sqlite:///{(tmp_path / 'db' / 't.db').as_posix()}",
                    data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", host="127.0.0.1", port=0,
                    worker=worker)


def fake_runner(input_path, out_dir, progress=None, **options):
    assert input_path.is_file()
    out_dir.mkdir(parents=True, exist_ok=True)
    progress("transcribe", 0.3)
    (out_dir / "clip_01.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return {"clips": [{"id": "clip_01", "start": 1.0, "end": 21.0, "score": 0.8, "title": "Hook",
                       "text": "Hook text.", "file": "clip_01.mp4", "output_duration": 19.5}]}


def failing_runner(input_path, out_dir, progress=None, **options):
    progress("transcribe", 0.1)
    raise RuntimeError("boom")


def test_migrations_create_tables(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    init_db(engine)
    init_db(engine)  # idempotent
    names = set(inspect(engine).get_table_names())
    assert {"projects", "jobs", "clips", "alembic_version"} <= names


def test_claim_is_exclusive_and_stale_jobs_requeue(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'q.db'}")
    init_db(engine)
    with session_factory(engine)() as s:
        s.add(Project(name="p", source_file="source.mp4"))
        s.commit()
    a, b = enqueue(engine, 1).id, enqueue(engine, 1).id
    assert claim_next(engine) == a
    assert claim_next(engine) == b
    assert claim_next(engine) is None
    assert requeue_stale(engine) == 2
    assert claim_next(engine) == a


def test_upload_runs_job_and_lists_clips(tmp_path):
    app = create_app(_settings(tmp_path), runner=fake_runner)
    client = TestClient(app)
    r = client.post("/api/projects", files={"file": ("My talk.mp4", b"fake video bytes", "video/mp4")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert client.get(f"/api/jobs/{body['job_id']}").json()["status"] == "queued"
    assert app.state.worker.run_once() == "done"
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] == "done" and job["progress"] == 1.0
    p = client.get(f"/api/projects/{body['project_id']}").json()
    assert p["name"] == "My talk"
    assert [c["key"] for c in p["clips"]] == ["clip_01"] and p["clips"][0]["duration"] == 19.5
    media = client.get(p["clips"][0]["url"])
    assert media.status_code == 200 and media.headers["content-type"] == "video/mp4"


def test_failed_job_reports_error(tmp_path):
    app = create_app(_settings(tmp_path), runner=failing_runner)
    client = TestClient(app)
    body = client.post("/api/projects", files={"file": ("a.mov", b"x", "video/quicktime")}).json()
    assert app.state.worker.run_once() == "failed"
    job = client.get(f"/api/jobs/{body['job_id']}").json()
    assert job["status"] == "failed" and job["error"].startswith("RuntimeError: boom")
    assert app.state.worker.run_once() is None


def test_upload_rejects_non_video_and_bad_paths(tmp_path):
    client = TestClient(create_app(_settings(tmp_path), runner=fake_runner))
    assert client.post("/api/projects", files={"file": ("notes.txt", b"x", "text/plain")}).status_code == 400
    assert client.get("/api/jobs/999").status_code == 404
    assert client.get("/media/1/clips/..%2Fsource.mp4").status_code == 404


def test_background_worker_with_real_pipeline(tmp_path, tiny_video):
    """The worker thread picks up an upload and runs the real pipeline (no transcription)."""
    from clipmaker.jobs import default_runner

    def runner(i, o, progress=None, **opts):
        return default_runner(i, o, progress=progress, transcribe=False)

    app = create_app(_settings(tmp_path, worker=True), runner=runner)
    app.state.worker.poll = 0.1
    with TestClient(app) as client:
        body = client.post("/api/projects", files={"file": ("tiny.mp4", tiny_video.read_bytes(), "video/mp4")}).json()
        for _ in range(100):
            job = client.get(f"/api/jobs/{body['job_id']}").json()
            if job["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
    assert job["status"] == "done", job
    clips_json = tmp_path / "data" / "projects" / str(body["project_id"]) / "clips" / "clips.json"
    assert json.loads(clips_json.read_text())["clips"] == []


def test_project_gallery_page(tmp_path):
    app = create_app(_settings(tmp_path), runner=fake_runner)
    client = TestClient(app)
    body = client.post("/api/projects", files={"file": ("שיחה.mp4", b"x", "video/mp4")}).json()
    pending = client.get(f"/projects/{body['project_id']}")
    assert pending.status_code == 200 and "Still working" in pending.text
    app.state.worker.run_once()
    page = client.get(f"/projects/{body['project_id']}").text
    assert "שיחה" in page and "<video" in page and "/media/1/clips/clip_01.mp4" in page
    assert ">80<" in page and "Hook text." in page
    index = client.get("/").text
    assert 'href="/projects/1"' in index and "1 clip" in index
    assert client.get("/projects/42").status_code == 404


def test_download_single_and_zip(tmp_path):
    import io
    import zipfile

    app = create_app(_settings(tmp_path), runner=fake_runner)
    client = TestClient(app)
    body = client.post("/api/projects", files={"file": ("Talk.mp4", b"x", "video/mp4")}).json()
    pid = body["project_id"]
    assert client.get(f"/projects/{pid}/clips.zip").status_code == 404  # nothing rendered yet
    app.state.worker.run_once()
    one = client.get(f"/media/{pid}/clips/clip_01.mp4?download=1")
    assert one.status_code == 200 and 'filename="Talk_clip_01.mp4"' in one.headers["content-disposition"]
    z = client.get(f"/projects/{pid}/clips.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(z.content)).namelist() == ["01_clip_01.mp4"]
    page = client.get(f"/projects/{pid}").text
    assert f"/projects/{pid}/clips.zip" in page and "?download=1" in page


def test_clips_are_stored_as_soon_as_they_are_rendered(tmp_path):
    seen: list[int] = []
    holder: dict = {}

    def runner(input_path, out_dir, progress=None, on_clip=None, **options):
        out_dir.mkdir(parents=True, exist_ok=True)
        clips = []
        for n in (1, 2):
            c = {"id": f"clip_0{n}", "start": 10.0 * n, "end": 10.0 * n + 5, "score": 0.5 + n / 10,
                 "title": "", "text": "x.", "file": f"clip_0{n}.mp4"}
            (out_dir / c["file"]).write_bytes(b"\x00")
            on_clip(c)
            clips.append(c)
            # the first clip is visible through the API while the job is still running
            seen.append(holder["client"].get(f"/api/jobs/{holder['job']}").json()["clips_ready"])
        return {"clips": clips}

    app = create_app(_settings(tmp_path), runner=runner)
    client = TestClient(app)
    holder["client"] = client
    body = client.post("/api/projects", files={"file": ("a.mp4", b"bytes", "video/mp4")}).json()
    holder["job"] = body["job_id"]
    assert app.state.worker.run_once() == "done"
    assert seen == [1, 2]
    p = client.get(f"/api/projects/{body['project_id']}").json()
    assert sorted(c["key"] for c in p["clips"]) == ["clip_01", "clip_02"]  # not stored twice
    assert client.get(f"/api/jobs/{body['job_id']}").json()["clips_ready"] == 2
