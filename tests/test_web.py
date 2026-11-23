from fastapi.testclient import TestClient

from clipmaker.config import Settings
from clipmaker.web.app import create_app


def _client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'db' / 't.db').as_posix()}",
        data_dir=tmp_path, cache_dir=tmp_path / "cache", host="127.0.0.1", port=0,
    )
    return TestClient(create_app(settings))


def test_health(tmp_path):
    r = _client(tmp_path).get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] is True


def test_index_page(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "ClipMaker" in r.text


def _serve_file(path):
    import http.server
    import threading

    body = path.read_bytes()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/talk.mp4":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_import_link_downloads_in_job(tmp_path, tiny_video, monkeypatch):
    from clipmaker.jobs import project_dir

    monkeypatch.setenv("CLIPMAKER_ALLOW_PRIVATE_URLS", "1")
    calls, stages = [], []

    def runner(src, out, progress=None, on_clip=None, **opts):
        calls.append((src, opts))
        return {"clips": []}

    settings = Settings(database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}", data_dir=tmp_path,
                        cache_dir=tmp_path / "cache", host="127.0.0.1", port=0, worker=False)
    app = create_app(settings, runner=runner)
    client = TestClient(app)
    srv, base = _serve_file(tiny_video)
    try:
        r = client.post("/api/projects/link", json={"url": f"{base}/talk.mp4"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "talk"
        import clipmaker.jobs as jobs
        orig = jobs._set_progress
        monkeypatch.setattr(jobs, "_set_progress", lambda e, j, st, f: (stages.append(st), orig(e, j, st, f)))
        assert app.state.worker.run_once() == "done"
    finally:
        srv.shutdown()
    src, opts = calls[0]
    assert src == project_dir(tmp_path, body["project_id"]) / "source.mp4"
    assert src.read_bytes() == tiny_video.read_bytes() and "source_url" not in opts
    assert "download" in stages
    assert client.get(f"/api/jobs/{body['job_id']}").json()["status"] == "done"


def test_import_link_failure_is_user_readable(tmp_path, monkeypatch, tiny_video):
    monkeypatch.setenv("CLIPMAKER_ALLOW_PRIVATE_URLS", "1")
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}", data_dir=tmp_path,
                        cache_dir=tmp_path / "cache", host="127.0.0.1", port=0, worker=False)
    app = create_app(settings, runner=lambda *a, **k: {"clips": []})
    client = TestClient(app)
    srv, base = _serve_file(tiny_video)
    try:
        job_id = client.post("/api/projects/link", json={"url": f"{base}/missing.mp4"}).json()["job_id"]
        assert app.state.worker.run_once() == "failed"
    finally:
        srv.shutdown()
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["error"].startswith("The server answered 404")


def test_import_link_rejects_bad_links(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPMAKER_ALLOW_PRIVATE_URLS", raising=False)
    client = _client(tmp_path)
    for url, needle in [("not a link", "http"), ("https://www.youtube.com/watch?v=x", "YouTube"),
                        ("http://127.0.0.1:9/a.mp4", "private or internal")]:
        r = client.post("/api/projects/link", json={"url": url})
        assert r.status_code == 400 and needle in r.json()["detail"], (url, r.text)
