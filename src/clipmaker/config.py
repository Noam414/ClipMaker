"""Runtime settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines); never overrides real env vars."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path
    cache_dir: Path
    host: str
    port: int


def get_settings() -> Settings:
    _load_dotenv(Path.cwd() / ".env")
    data_dir = Path(os.environ.get("CLIPMAKER_DATA_DIR", "./data")).expanduser()
    return Settings(
        database_url=os.environ.get("DATABASE_URL", f"sqlite:///{(data_dir / 'clipmaker.db').as_posix()}"),
        data_dir=data_dir,
        cache_dir=Path(os.environ.get("CLIPMAKER_CACHE_DIR", "./.cache")).expanduser(),
        host=os.environ.get("CLIPMAKER_HOST", "127.0.0.1"),
        port=int(os.environ.get("CLIPMAKER_PORT", "8000")),
    )
