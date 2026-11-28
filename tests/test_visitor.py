"""Free-tier guardrails part 2 (D-030): visitor cookie, private projects, daily limit, queue position."""
from __future__ import annotations

from fastapi.testclient import TestClient

from clipmaker import visitor as vis
from clipmaker.config import Settings
from clipmaker.web.app import create_app

VIDEO = ("talk.mp4", b"0" * 100, "video/mp4")


def _app(tmp_path):
    return create_app(Settings(database_url=f"sqlite:///{(tmp_path / 'db' / 't.db').as_posix()}",
                               data_dir=tmp_path, cache_dir=tmp_path / "cache", host="127.0.0.1",
                               port=0, worker=False))


def test_sign_and_verify(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPMAKER_SECRET_KEY", raising=False)
    key = vis.secret_key(tmp_path)
    assert vis.secret_key(tmp_path) == key and len(key) == 64  # kept in <data>/secret_key
    assert (tmp_path / "secret_key").stat().st_mode & 0o077 == 0
    vid = vis.new_id()
    assert vis.verify(key, vis.sign(key, vid)) == vid
    assert vis.verify(b"other", vis.sign(key, vid)) is None
    assert vis.verify(key, vid + ".0") is None and vis.verify(key, None) is None
    assert vis.verify(key, "x.y.z") is None and vis.verify(key, "zz." + "0" * 32) is None
    monkeypatch.setenv("CLIPMAKER_SECRET_KEY", "from-env")
    assert vis.secret_key(tmp_path) == b"from-env"


def test_cookie_set_once_and_kept(tmp_path):
    client = TestClient(_app(tmp_path))
    r = client.get("/")
    cookie = r.cookies.get(vis.COOKIE)
    assert cookie and "httponly" in r.headers["set-cookie"].lower()
    assert vis.COOKIE not in client.get("/").cookies  # valid cookie: not re-issued
    client.cookies.set(vis.COOKIE, "forged." + "0" * 32)
    assert client.get("/").cookies.get(vis.COOKIE) not in (None, cookie)


def test_private_mode_hides_other_visitors_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPMAKER_PRIVATE", "1")
    app = _app(tmp_path)
    alice, bob = TestClient(app), TestClient(app)
    body = alice.post("/api/projects", files={"file": ("alice-secret.mp4", *VIDEO[1:])}).json()
    pid, job = body["project_id"], body["job_id"]
    assert "alice-secret" in alice.get("/").text
    assert "alice-secret" not in bob.get("/").text
    for url in (f"/projects/{pid}", f"/api/projects/{pid}", f"/api/jobs/{job}",
                f"/projects/{pid}/clips.zip", f"/media/{pid}/clips/clip_01.mp4"):
        assert bob.get(url).status_code == 404, url
    assert alice.get(f"/projects/{pid}").status_code == 200
    assert alice.get(f"/api/jobs/{job}").status_code == 200
    monkeypatch.setenv("CLIPMAKER_PRIVATE", "0")  # local use: one person sees everything
    assert "alice-secret" in bob.get("/").text and bob.get(f"/projects/{pid}").status_code == 200


def test_daily_limit_per_visitor_and_ip(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPMAKER_DAILY_LIMIT", "2")
    app = _app(tmp_path)
    a = TestClient(app)
    assert a.post("/api/projects", files={"file": VIDEO}).status_code == 201
    assert a.post("/api/projects", files={"file": VIDEO}).status_code == 201
    r = a.post("/api/projects", files={"file": VIDEO})
    assert r.status_code == 429 and "limit of 2 videos" in r.json()["detail"]
    assert a.post("/api/projects/link", json={"url": "https://example.com/x.mp4"}).status_code == 429
    # clearing cookies doesn't help: same IP ("testclient")
    assert TestClient(app).post("/api/projects", files={"file": VIDEO}).status_code == 429
    monkeypatch.setenv("CLIPMAKER_DAILY_LIMIT", "0")
    assert a.post("/api/projects", files={"file": VIDEO}).status_code == 201


def test_queue_position(tmp_path):
    client = TestClient(_app(tmp_path))
    jobs = [client.post("/api/projects", files={"file": VIDEO}).json()["job_id"] for _ in range(3)]
    assert [client.get(f"/api/jobs/{j}").json()["queue_ahead"] for j in jobs] == [0, 1, 2]
    from sqlalchemy.orm import Session

    from clipmaker.store import Job

    with Session(client.app.state.engine) as s:
        s.get(Job, jobs[0]).status = "running"
        s.commit()
    status = client.get(f"/api/jobs/{jobs[0]}").json()
    assert "queue_ahead" not in status
    assert [client.get(f"/api/jobs/{j}").json()["queue_ahead"] for j in jobs[1:]] == [1, 2]
