from clipmaker.config import get_settings


def test_defaults_use_sqlite(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CLIPMAKER_DATA_DIR", str(tmp_path / "d"))
    s = get_settings()
    assert s.database_url.startswith("sqlite:///")
    assert s.database_url.endswith("clipmaker.db")


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("CLIPMAKER_PORT", "9123")
    s = get_settings()
    assert s.database_url == "postgresql+psycopg://u:p@h/db"
    assert s.port == 9123


def test_dotenv_does_not_override_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("CLIPMAKER_HOST=0.0.0.0\nCLIPMAKER_PORT=7000\n")
    monkeypatch.delenv("CLIPMAKER_HOST", raising=False)
    monkeypatch.setenv("CLIPMAKER_PORT", "8001")
    s = get_settings()
    assert s.host == "0.0.0.0"
    assert s.port == 8001
