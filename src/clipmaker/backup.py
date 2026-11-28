"""Database backups for a self-hosted server (M7 deploy kit, D-028).

`clipmaker backup` copies the SQLite database with SQLite's online backup API, so it is
safe while the web app and the worker are writing, gzips the copy into the backup
directory and keeps only the newest N files. Uploaded videos and rendered clips are not
backed up: they are big and, with the free-tier guardrails, deleted after a day anyway.
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PREFIX = "clipmaker-"
SUFFIX = ".db.gz"


def sqlite_path(database_url: str) -> Path:
    """The file behind a `sqlite:///...` URL; other databases have their own tools."""
    for scheme in ("sqlite+pysqlite:///", "sqlite:///"):
        if database_url.startswith(scheme):
            path = database_url[len(scheme):].split("?", 1)[0]
            if path and path != ":memory:":
                return Path(path)
    raise ValueError("clipmaker backup only handles a SQLite file database; "
                     "for PostgreSQL use pg_dump")


def backup_sqlite(db_file: Path, out_dir: Path, keep: int = 14, now: datetime | None = None) -> Path:
    """Write a consistent gzipped copy of `db_file` into `out_dir`, delete older copies
    beyond `keep`, and return the new file's path."""
    if not db_file.is_file():
        raise FileNotFoundError(f"database not found: {db_file}")
    if keep < 1:
        raise ValueError("keep must be at least 1")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{PREFIX}{stamp}{SUFFIX}"
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        snapshot = Path(tmp) / "snapshot.db"
        src = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        check = sqlite3.connect(snapshot)
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("the backup copy failed SQLite's integrity check")
        finally:
            check.close()
        partial = target.with_name(target.name + ".part")
        with open(snapshot, "rb") as f_in, gzip.open(partial, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        partial.replace(target)
    for old in sorted(out_dir.glob(f"{PREFIX}*{SUFFIX}"))[:-keep]:
        old.unlink()
    return target


def restore_sqlite(backup_file: Path, db_file: Path) -> None:
    """Unpack a backup over `db_file` (stop the app first). The old file is kept as .before-restore."""
    if db_file.exists():
        db_file.replace(db_file.with_name(db_file.name + ".before-restore"))
    # a leftover WAL from the old database would be replayed onto the restored one
    for side in ("-wal", "-shm"):
        extra = db_file.with_name(db_file.name + side)
        if extra.exists():
            extra.replace(db_file.with_name(db_file.name + side + ".before-restore"))
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(backup_file, "rb") as f_in, open(db_file, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
