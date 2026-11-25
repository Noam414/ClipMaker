"""Import a video from a direct media link (http(s)://.../video.mp4), safely.

Guards (M5.5, D-018):
- only http and https; no credentials in the link
- every address the host name resolves to must be a public (global) address, so links
  can't reach the server itself, its LAN or cloud metadata endpoints (SSRF); the same
  check runs again on every redirect, and redirects are capped
- a size cap (Content-Length when given, and always while streaming) and a length cap
  (ffprobe on the downloaded file)

- DNS rebinding is closed by pinning (D-022): every connection, including after redirects,
  resolves the name, checks the addresses and then connects to exactly the address it
  checked. The HTTP client never resolves the name again, so a host can't answer "public"
  for the check and "127.0.0.1" for the connection. TLS still verifies the certificate
  against the host name (SNI), and the Host header is unchanged.

`CLIPMAKER_ALLOW_PRIVATE_URLS=1` lifts the address check, for tests against a local server.
Known limit: when an outbound proxy is configured (HTTP(S)_PROXY), the proxy resolves names,
so pinning can't apply; the check before the request still runs.
"""
from __future__ import annotations

import http.client
import ipaddress
import logging
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from clipmaker.media import probe

log = logging.getLogger(__name__)

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
    checked_addresses(parts.hostname, port)
    return url


def _check_ip(address: str) -> None:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast:
        raise FetchError("Links to private or internal addresses aren't allowed.")


def checked_addresses(host: str, port: int) -> list[tuple]:
    """Resolve `host` and return its (family, sockaddr) pairs; FetchError if any is private."""
    try:
        infos = _resolve(host, port)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError(f"Can't find the host {host!r}.") from exc
    if not infos:
        raise FetchError(f"Can't find the host {host!r}.")
    for info in infos:
        _check_ip(info[4][0])
    return [(info[0], info[4]) for info in infos]


def _connect_pinned(host: str, port: int, timeout, source_address=None) -> socket.socket:
    """Connect to an address of `host` that was checked in this same call (no second lookup)."""
    last = None
    for family, sockaddr in checked_addresses(host, port):
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            sock.close()
            last = exc
    raise last or OSError(f"can't connect to {host}")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _connect_pinned(self.host, self.port, self.timeout, self.source_address)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = _connect_pinned(self.host, self.port, self.timeout, self.source_address)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


def _proxy_configured() -> bool:
    proxies = urllib.request.getproxies()
    return bool(proxies.get("http") or proxies.get("https"))


def build_opener() -> urllib.request.OpenerDirector:
    """An opener that re-checks redirects and, unless a proxy or the private-address
    override is configured, connects only to the address it just checked (D-022)."""
    if _private_allowed():
        return urllib.request.build_opener(_CheckedRedirects())
    if _proxy_configured():
        log.warning("outbound proxy configured: link downloads can't pin checked addresses")
        return urllib.request.build_opener(_CheckedRedirects())
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _PinnedHTTPHandler(),
                                       _PinnedHTTPSHandler(), _CheckedRedirects())


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
    opener = build_opener()
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
