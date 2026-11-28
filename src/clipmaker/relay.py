"""A loopback relay so ffmpeg can read a direct link without bypassing the SSRF guards.

ffmpeg's own HTTP client resolves names and follows redirects itself, so pointing it at a
user's link would skip `fetch`'s address checks and pinning (D-018, D-022). Instead ffmpeg
reads `http://127.0.0.1:<port>/media` and every request it makes (including `Range`
requests to seek) is forwarded through `fetch.build_opener()`: checked, pinned and with
checked redirects. Only that one upstream URL is reachable through the relay, and a byte
budget caps how much may be fetched in total (M5.5: audio first, then only the selected
sections of a direct link).
"""
from __future__ import annotations

import http.server
import logging
import secrets
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

from clipmaker import fetch

log = logging.getLogger(__name__)

_PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges",
                 "Last-Modified", "ETag")


class RelayBudgetExceeded(fetch.FetchError):
    pass


class _Relay:
    def __init__(self, url: str, max_bytes: int):
        self.url = fetch.check_url(url)
        self.max_bytes = max_bytes
        self.bytes = 0
        self.requests = 0
        self.error: Exception | None = None
        self.opener = fetch.build_opener()
        self.path = f"/{secrets.token_urlsafe(8)}/media"
        self.lock = threading.Lock()


def _handler(relay: _Relay):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def setup(self):
            # small send buffer: when ffmpeg seeks away it drops this connection, and
            # whatever we pulled from upstream into a big buffer was fetched for nothing
            import socket
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 128 * 1024)
            super().setup()

        def _forward(self, head_only: bool):
            if self.path != relay.path:
                self.send_error(404)
                return
            with relay.lock:
                relay.requests += 1
            headers = {"User-Agent": fetch.USER_AGENT}
            if self.headers.get("Range"):
                headers["Range"] = self.headers["Range"]
            req = urllib.request.Request(relay.url, headers=headers,
                                         method="HEAD" if head_only else "GET")
            try:
                resp = relay.opener.open(req, timeout=fetch.TIMEOUT_S)
            except urllib.error.HTTPError as exc:
                self.send_response(exc.code)
                for h in ("Content-Range",):
                    if exc.headers.get(h):
                        self.send_header(h, exc.headers[h])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            except Exception as exc:  # blocked address, DNS failure, timeout…
                relay.error = exc if isinstance(exc, fetch.FetchError) else fetch.FetchError(
                    f"Couldn't download the link: {getattr(exc, 'reason', exc)}.")
                self.send_error(502)
                return
            with resp:
                self.send_response(resp.status)
                for h in _PASS_HEADERS:
                    if resp.headers.get(h):
                        self.send_header(h, resp.headers[h])
                if not resp.headers.get("Content-Length"):
                    self.send_header("Connection", "close")
                    self.close_connection = True
                self.end_headers()
                if head_only:
                    return
                try:
                    while chunk := resp.read(64 * 1024):
                        with relay.lock:
                            relay.bytes += len(chunk)
                            over = relay.bytes > relay.max_bytes
                        if over:
                            relay.error = RelayBudgetExceeded(
                                f"The file is too big (limit {relay.max_bytes // 1024 ** 2} MB).")
                            self.close_connection = True
                            return
                        self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True  # ffmpeg hangs up after a seek or at -to

        def do_GET(self):
            self._forward(head_only=False)

        def do_HEAD(self):
            self._forward(head_only=True)

    return Handler


@contextmanager
def relay(url: str, max_bytes: int = fetch.MAX_BYTES):
    """Serve `url` on loopback for ffmpeg; yields an object with `.local_url`, `.bytes`,
    `.requests` and `.error` (the FetchError that stopped a request, if any)."""
    r = _Relay(url, max_bytes)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler(r))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    r.local_url = f"http://127.0.0.1:{srv.server_address[1]}{r.path}"
    try:
        yield r
    finally:
        srv.shutdown()
        srv.server_close()


# ffmpeg may only use plain HTTP to the loopback relay: no file:, no other hosts via
# references inside the container, no HLS/DASH sub-requests (those get their own item)
FFMPEG_INPUT = ["-protocol_whitelist", "http,tcp"]


def _run_ffmpeg(cmd: list[str], r) -> None:
    import subprocess

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=fetch.TIMEOUT_S * 20)
    if r.error:
        raise r.error
    if proc.returncode != 0:
        log.warning("ffmpeg via relay failed (exit %s): %s", proc.returncode, proc.stderr[-1500:])
        raise fetch.FetchError("The link isn't a video or audio file ffmpeg can read.")


def fetch_audio(url: str, dest, max_bytes: int = fetch.MAX_BYTES,
                max_seconds: float = fetch.MAX_SECONDS):
    """Save only the first audio track of a direct link (`-vn`, stream copy) to `dest`
    (.m4a). For a single MP4 the video bytes still pass through (they're interleaved), but
    nothing but the audio is written to disk."""
    from pathlib import Path

    from clipmaker.media import probe, require_tool

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part" + dest.suffix)
    try:
        with relay(url, max_bytes) as r:
            _run_ffmpeg([require_tool("ffmpeg"), "-v", "error", "-y", *FFMPEG_INPUT,
                         "-i", r.local_url, "-map", "0:a:0", "-vn", "-c:a", "copy",
                         "-t", str(max_seconds + 1), str(tmp)], r)
        info = probe(tmp)
        if info.duration > max_seconds:
            raise fetch.FetchError(f"The video is too long (limit {int(max_seconds // 60)} minutes).")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    return dest


def fetch_section(url: str, start: float, end: float, dest, max_bytes: int = fetch.MAX_BYTES):
    """Save only [start, end) of a direct link, video + audio, to `dest` (.mp4).

    `-ss` before `-i` makes ffmpeg seek with HTTP Range requests, so only about the
    section's bytes (plus the index) are fetched. Re-encoding makes t=0 exactly `start`,
    like the frame-accurate yt-dlp sections (D-020)."""
    from pathlib import Path

    from clipmaker.media import require_tool

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.stem + ".part" + dest.suffix)
    try:
        with relay(url, max_bytes) as r:
            _run_ffmpeg([require_tool("ffmpeg"), "-v", "error", "-y", *FFMPEG_INPUT,
                         "-ss", f"{start:.3f}", "-i", r.local_url, "-t", f"{end - start:.3f}",
                         "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset",
                         "superfast", "-crf", "18", "-c:a", "aac", "-b:a", "160k",
                         str(tmp)], r)
            fetched = r.bytes
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    log.info("section %.1f-%.1f s: %d bytes fetched", start, end, fetched)
    return dest
