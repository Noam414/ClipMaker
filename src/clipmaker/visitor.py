"""Anonymous visitors on a public server (free-tier guardrails part 2, D-030).

Every browser gets a random visitor id in a signed, HttpOnly cookie. Projects remember the
visitor and the client IP that created them, which gives:

- `CLIPMAKER_PRIVATE=1` (the Oracle kit sets it): the home page lists only your own projects,
  and project pages, clips, zips and job status answer 404 to anyone else. Off locally,
  where one person uses the app and sees everything, as before.
- `CLIPMAKER_DAILY_LIMIT=N` (0 = off; the kit sets 5): at most N new videos per visitor
  **or** IP in 24 hours (the IP part still counts when cookies are cleared).

The signing key is `CLIPMAKER_SECRET_KEY`, or a random key kept in `<data>/secret_key`.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

COOKIE = "cm_visitor"
MAX_AGE = 365 * 24 * 3600


def _flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")


def private_mode() -> bool:
    return _flag("CLIPMAKER_PRIVATE")


def daily_limit() -> int:
    return int(os.environ.get("CLIPMAKER_DAILY_LIMIT", "0") or 0)


def secret_key(data_dir: Path) -> bytes:
    env = os.environ.get("CLIPMAKER_SECRET_KEY", "").strip()
    if env:
        return env.encode()
    path = Path(data_dir) / "secret_key"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"secret_key.{secrets.token_hex(4)}.part")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(32))
        try:
            os.link(tmp, path)  # first writer wins if two processes race
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    return path.read_text().strip().encode()


def _mac(key: bytes, vid: str) -> str:
    return hmac.new(key, vid.encode(), hashlib.sha256).hexdigest()[:32]


def sign(key: bytes, vid: str) -> str:
    return f"{vid}.{_mac(key, vid)}"


def verify(key: bytes, cookie: str | None) -> str | None:
    """The visitor id inside a valid cookie, else None."""
    if not cookie or cookie.count(".") != 1:
        return None
    vid, mac = cookie.split(".")
    if len(vid) != 32 or not all(c in "0123456789abcdef" for c in vid):
        return None
    return vid if hmac.compare_digest(mac, _mac(key, vid)) else None


def new_id() -> str:
    return secrets.token_hex(16)
