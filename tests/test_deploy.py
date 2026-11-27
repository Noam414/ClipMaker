"""M7 deploy kit (deploy/oracle/, D-028): database backups, setup helpers, config files."""
from __future__ import annotations

import gzip
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipmaker.backup import backup_sqlite, restore_sqlite, sqlite_path
from clipmaker.cli import main

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "deploy" / "oracle"


def _make_db(path: Path, rows: int = 100) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
    con.commit()
    con.close()


def _unpack_count(backup: Path, tmp: Path) -> int:
    out = tmp / "check.db"
    with gzip.open(backup, "rb") as f_in, open(out, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    con = sqlite3.connect(out)
    try:
        return con.execute("SELECT count(*) FROM t").fetchone()[0]
    finally:
        con.close()
        out.unlink()


def test_sqlite_path():
    assert sqlite_path("sqlite:////data/clipmaker.db") == Path("/data/clipmaker.db")
    assert sqlite_path("sqlite:///./data/x.db?timeout=5") == Path("./data/x.db")
    with pytest.raises(ValueError):
        sqlite_path("postgresql+psycopg://u:p@h/db")
    with pytest.raises(ValueError):
        sqlite_path("sqlite:///:memory:")


def test_backup_includes_wal_writes_and_rotates(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db, rows=50)
    # an open writer with uncheckpointed WAL pages: the copy must still see them
    writer = sqlite3.connect(db)
    writer.execute("INSERT INTO t (v) VALUES ('in-wal')")
    writer.commit()
    out = tmp_path / "backups"
    t0 = datetime(2026, 11, 27, 3, 30, tzinfo=timezone.utc)
    made = [backup_sqlite(db, out, keep=3, now=t0 + timedelta(days=d)) for d in range(5)]
    writer.close()
    kept = sorted(p.name for p in out.iterdir())
    assert kept == [p.name for p in made[-3:]]
    assert kept[-1] == "clipmaker-20261201-033000.db.gz"
    assert _unpack_count(made[-1], tmp_path) == 51
    assert not list(out.glob("*.part")) and not [p for p in out.iterdir() if p.is_dir()]


def test_backup_while_another_thread_writes(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db, rows=10)
    stop = threading.Event()

    def write():
        con = sqlite3.connect(db, timeout=10)
        while not stop.is_set():
            con.execute("INSERT INTO t (v) VALUES ('x')")
            con.commit()
        con.close()

    th = threading.Thread(target=write)
    th.start()
    try:
        backup = backup_sqlite(db, tmp_path / "b")
    finally:
        stop.set()
        th.join()
    assert _unpack_count(backup, tmp_path) >= 10  # consistent copy, passed integrity_check


def test_restore_keeps_previous_file(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db, rows=7)
    backup = backup_sqlite(db, tmp_path / "b")
    sqlite3.connect(db).execute("DELETE FROM t").connection.commit()
    restore_sqlite(backup, db)
    assert sqlite3.connect(db).execute("SELECT count(*) FROM t").fetchone()[0] == 7
    assert (tmp_path / "app.db.before-restore").is_file()


def test_backup_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_sqlite(tmp_path / "missing.db", tmp_path / "b")
    db = tmp_path / "app.db"
    _make_db(db)
    with pytest.raises(ValueError):
        backup_sqlite(db, tmp_path / "b", keep=0)


def test_cli_backup(tmp_path, monkeypatch, capsys):
    db = tmp_path / "data" / "clipmaker.db"
    db.parent.mkdir()
    _make_db(db, rows=3)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    monkeypatch.setenv("CLIPMAKER_DATA_DIR", str(tmp_path / "data"))
    assert main(["backup", "--keep", "2"]) == 0
    printed = Path(capsys.readouterr().out.strip())
    assert printed.parent == tmp_path / "data" / "backups" and printed.is_file()
    monkeypatch.setenv("DATABASE_URL", "postgresql://h/db")
    assert main(["backup"]) == 1
    assert "pg_dump" in capsys.readouterr().err


def _bash(script: str) -> str:
    cmd = f"CLIPMAKER_SETUP_SOURCE_ONLY=1 source '{KIT / 'setup.sh'}'; {script}"
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=True).stdout


def test_setup_helpers(tmp_path):
    assert _bash("sslip_name 140.238.1.25") == "140-238-1-25.sslip.io\n"
    assert subprocess.run(["bash", "-c", f"CLIPMAKER_SETUP_SOURCE_ONLY=1 source '{KIT / 'setup.sh'}'; "
                           "sslip_name 'not-an-ip'"]).returncode != 0
    env = tmp_path / ".env"
    shutil.copy(KIT / "env.example", env)
    _bash(f"env_set CLIPMAKER_DOMAIN clips.example.org '{env}'; env_set NEW_KEY 1 '{env}'")
    assert _bash(f"env_get CLIPMAKER_DOMAIN '{env}'") == "clips.example.org\n"
    assert _bash(f"env_get CLIPMAKER_DATA_PATH '{env}'") == "/srv/clipmaker/data\n"
    assert env.read_text().count("CLIPMAKER_DOMAIN=") == 1 and "NEW_KEY=1" in env.read_text()


def test_setup_script_is_clean_bash():
    subprocess.run(["bash", "-n", str(KIT / "setup.sh")], check=True)
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", str(KIT / "setup.sh")], check=True)


def test_compose_file(tmp_path):
    text = (KIT / "compose.yml").read_text()
    # only Caddy is published; the app and the PO-token server stay on the internal network
    assert text.count('"80:80"') == 1 and "8000:8000" not in text and "4416:4416" not in text
    assert "CLIPMAKER_YTDLP_POT_URL: http://pot:4416" in text
    assert "reverse_proxy clipmaker:8000" in (KIT / "Caddyfile").read_text()
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode if shutil.which("docker") else 1:
        pytest.skip("docker compose not available")
    out = subprocess.run(["docker", "compose", "-f", str(KIT / "compose.yml"), "config", "--format", "json"],
                         capture_output=True, text=True, check=True).stdout
    import json

    cfg = json.loads(out)
    assert set(cfg["services"]) == {"clipmaker", "pot", "caddy"}
    assert cfg["services"]["clipmaker"]["build"]["args"]["WITH_YOUTUBE"] == "1"
    assert "ports" not in cfg["services"]["clipmaker"]
