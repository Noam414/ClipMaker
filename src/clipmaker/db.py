"""Database access. Everything goes through one DATABASE_URL (SQLite by default)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def make_engine(database_url: str) -> Engine:
    kwargs = {}
    if database_url.startswith("sqlite:///"):
        db_path = database_url.removeprefix("sqlite:///")
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, **kwargs)


def ping(engine: Engine) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1")).scalar() == 1
