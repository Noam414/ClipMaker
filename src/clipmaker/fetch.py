"""Import a video from a direct media link (http(s)://.../video.mp4), safely.

Guards (M5.5, D-018):
- only http and https; no credentials in the link
- every address the host name resolves to must be a public (global) address, so links
  can't reach the server itself, its LAN or cloud metadata endpoints (SSRF); the same
  check runs again on every redirect, and redirects are capped
- a size cap (Content-Length when given, and always while streaming) and a length cap
  (ffprobe on the downloaded file)

`CLIPMAKER_ALLOW_PRIVATE_URLS=1` lifts the address check, for tests against a local server.
Known limit: the name is resolved once for the check and again by the HTTP client, so a
DNS-rebinding host could still switch addresses in between.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from clipmaker.media import probe

MAX_BYTES = 4 * 1024 ** 3  # 4 GB
MAX_SECONDS = 60 * 60  # D-013 guardrail: videos up to 60 minutes
MAX_REDIRECTS = 5
TIMEOUT_S = 30
USER_AGENT = "ClipMaker/0.1 (+https://github.com/Noam414/ClipMaker)"


class FetchError(Exception):
    """A link that can't or mustn't be imported; the message is safe to show to users."""


def is_url(value: str) -> bool:
    return urllib.parse.urlsplit(str(value)).scheme.lower() in ("http", "https")


def _private_allowed() -> bool:
    return os.environ.get("CLIPMAKER_ALLOW_PRIVATE_URLS") == "1"


def _resolve(host: str, port: int):
    return socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)


def check_url(url: str) -> str:
    """Raise FetchError unless `url` is an http(s) link to a public address; returns it."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise FetchError("Only http:// and https:// links are supported.")
    if not parts.hostname:
        raise FetchError("The link has no host name.")
    if parts.username or parts.password:
        raise FetchError("Links with a user name or password aren't supported.")
    try:
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise FetchError("The link has an invalid port.") from exc
    if _private_allowed():
        return url
    try:
        infos = _resolve(parts.hostname, port)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError(f"Can't find the host {parts.hostname!r}.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise FetchError("Links to private or internal addresses aren't allowed.")
    return url


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, dest: Path, max_bytes: int = MAX_BYTES, max_seconds: float = MAX_SECONDS,
             progress=None) -> Path:
    """Download `url` to `dest` within the limits, then check it's a video of allowed length.

    `progress(fraction)` is called while streaming when the server sends a Content-Length.
    """
    check_url(url)
    opener = urllib.request.build_opener(_CheckedRedirects())
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with opener.open(req, timeout=TIMEOUT_S) as resp:
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError(f"The file is too big (limit {max_bytes // 1024 ** 2} MB).")
            total = 0
            with open(tmp, "wb") as fh:
                while chunk := resp.read(1 << 20):
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError(f"The file is too big (limit {max_bytes // 1024 ** 2} MB).")
                    fh.write(chunk)
                    if progress and length and length.isdigit() and int(length) > 0:
                        progress(min(1.0, total / int(length)))
    except FetchError:
        tmp.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"The server answered {exc.code} {exc.reason}.") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"Couldn't download the link: {getattr(exc, 'reason', exc)}.") from exc
    try:
        info = probe(tmp)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise FetchError("The link isn't a video or audio file ffmpeg can read.") from exc
    if not (info.has_video or info.has_audio):
        tmp.unlink(missing_ok=True)
        raise FetchError("The link isn't a video or audio file.")
    if info.duration > max_seconds:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"The video is too long (limit {int(max_seconds // 60)} minutes).")
    os.replace(tmp, dest)
    return dest


def filename_from_url(url: str, default: str = "video.mp4") -> str:
    """A safe local file name from the link's path (keeps a media extension)."""
    name = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    name = "".join(ch for ch in name if ch.isalnum() or ch in "._- ")[:100].strip(" .")
    return name if Path(name).suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".m4a", ".mp3", ".wav") \
        else default
