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
