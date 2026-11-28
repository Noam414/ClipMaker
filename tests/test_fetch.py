import http.server
import socket
import threading
from pathlib import Path

import pytest

from clipmaker import fetch
from clipmaker.fetch import FetchError, check_url, download, filename_from_url


def _fake_dns(table):
    def resolve(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]
    return resolve


@pytest.mark.parametrize("url", [
    "ftp://example.com/a.mp4", "file:///etc/passwd", "http:///nohost", "http://user:pw@example.com/a.mp4",
])
def test_rejects_bad_schemes_and_credentials(url):
    with pytest.raises(FetchError):
        check_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.0.10", "169.254.169.254", "100.64.0.1",
                                "::1", "fe80::1", "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1"])
def test_rejects_private_and_internal_addresses(monkeypatch, ip):
    monkeypatch.delenv("CLIPMAKER_ALLOW_PRIVATE_URLS", raising=False)
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    monkeypatch.setattr(fetch, "_resolve", lambda h, p: [(family, socket.SOCK_STREAM, 6, "", (ip, p))])
    with pytest.raises(FetchError, match="private or internal"):
        check_url("https://media.example.com/v.mp4")


def test_accepts_public_address(monkeypatch):
    monkeypatch.delenv("CLIPMAKER_ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(fetch, "_resolve", _fake_dns({"cdn.example.com": "93.184.216.34"}))
    assert check_url("https://cdn.example.com/v.mp4") == "https://cdn.example.com/v.mp4"


def test_filename_from_url():
    assert filename_from_url("https://x.com/a/My%20Talk.mp4?sig=1") == "My Talk.mp4"
    assert filename_from_url("https://x.com/watch?v=1") == "video.mp4"
    assert filename_from_url("https://x.com/../../etc/passwd") == "video.mp4"


@pytest.fixture()
def server(tiny_video):
    data = Path(tiny_video).read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/video.mp4":
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/text.mp4":
                body = b"not a video at all" * 100
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/to-internal":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/video.mp4")
                self.end_headers()
            else:
                self.send_error(404)

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://localhost:{srv.server_port}", len(data)
    srv.shutdown()


def test_download_within_limits(server, tmp_path, monkeypatch):
    base, size = server
    monkeypatch.setenv("CLIPMAKER_ALLOW_PRIVATE_URLS", "1")
    out = download(f"{base}/video.mp4", tmp_path / "v.mp4")
    assert out.stat().st_size == size
    with pytest.raises(FetchError, match="too big"):
        download(f"{base}/video.mp4", tmp_path / "big.mp4", max_bytes=size - 1)
    with pytest.raises(FetchError, match="too long"):
        download(f"{base}/video.mp4", tmp_path / "long.mp4", max_seconds=0.5)
    with pytest.raises(FetchError, match="isn't a video"):
        download(f"{base}/text.mp4", tmp_path / "t.mp4")
    with pytest.raises(FetchError, match="404"):
        download(f"{base}/missing.mp4", tmp_path / "m.mp4")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["v.mp4"]  # no partial files left


FAKE_PUBLIC = "127.0.0.2"  # loopback, but treated as a public address in the pinned tests


@pytest.fixture()
def pinned(tiny_video, monkeypatch):
    """Strict mode (no proxy, no private override) with a server on 127.0.0.2 that the
    address check treats as public; returns (port, seen Host headers, dns table)."""
    for var in ("CLIPMAKER_ALLOW_PRIVATE_URLS", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy",
                "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    real_check = fetch._check_ip
    monkeypatch.setattr(fetch, "_check_ip", lambda a: None if a == FAKE_PUBLIC else real_check(a))
    data = Path(tiny_video).read_bytes()
    hosts = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hosts.append(self.headers.get("Host"))
            if self.path == "/video.mp4":
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/to-internal":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/video.mp4")
                self.end_headers()
            else:
                self.send_error(404)

        def log_message(self, *args):
            pass

    try:
        srv = http.server.ThreadingHTTPServer((FAKE_PUBLIC, 0), Handler)
    except OSError:
        pytest.skip("can't bind 127.0.0.2")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    table = {}

    def resolve(host, port):
        answers = table[host]
        ip = answers.pop(0) if len(answers) > 1 else answers[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(fetch, "_resolve", resolve)
    yield srv.server_port, hosts, table
    srv.shutdown()
    srv.server_close()


def test_pinned_download_connects_to_checked_address(pinned, tmp_path):
    port, hosts, table = pinned
    table["video.test"] = [FAKE_PUBLIC]  # not in real DNS: only the pinned connection can reach it
    out = download(f"http://video.test:{port}/video.mp4", tmp_path / "v.mp4")
    assert out.stat().st_size > 0
    assert hosts == [f"video.test:{port}"]  # Host header keeps the name


def test_dns_rebinding_is_blocked(pinned, tmp_path):
    port, hosts, table = pinned
    # public for the up-front check, then 127.0.0.1 for the connection
    table["rebind.test"] = [FAKE_PUBLIC, "127.0.0.1"]
    with pytest.raises(FetchError, match="private or internal"):
        download(f"http://rebind.test:{port}/video.mp4", tmp_path / "v.mp4")
    assert hosts == [] and not list(tmp_path.iterdir())


def test_redirect_to_internal_address_is_blocked(pinned, tmp_path):
    port, hosts, table = pinned
    table["video.test"] = [FAKE_PUBLIC]
    table["127.0.0.1"] = ["127.0.0.1"]
    with pytest.raises(FetchError, match="private or internal"):
        download(f"http://video.test:{port}/to-internal", tmp_path / "v.mp4")
    assert hosts == [f"video.test:{port}"]  # the redirect target was never contacted
    assert not list(tmp_path.iterdir())


def test_proxy_falls_back_to_unpinned_opener(monkeypatch):
    monkeypatch.delenv("CLIPMAKER_ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    handlers = {type(h) for h in fetch.build_opener().handlers}
    assert fetch._PinnedHTTPSHandler not in handlers
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    handlers = {type(h) for h in fetch.build_opener().handlers}
    assert {fetch._PinnedHTTPHandler, fetch._PinnedHTTPSHandler} <= handlers


def test_cli_reports_blocked_link(tmp_path, monkeypatch, capsys):
    from clipmaker.cli import main

    monkeypatch.delenv("CLIPMAKER_ALLOW_PRIVATE_URLS", raising=False)
    assert main(["http://127.0.0.1:9/video.mp4", "--out", str(tmp_path / "out")]) == 2
    assert "private or internal" in capsys.readouterr().err


def test_pinned_https_verifies_certificate_for_the_name(pinned, tmp_path):
    import shutil
    import ssl
    import subprocess
    import urllib.request

    if not shutil.which("openssl"):
        pytest.skip("openssl missing")
    port, hosts, table = pinned
    table["video.test"] = [FAKE_PUBLIC]
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=video.test", "-addext", "subjectAltName=DNS:video.test",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    srv = http.server.HTTPServer((FAKE_PUBLIC, 0), Handler)
    srv.socket = server_ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        client_ctx = ssl.create_default_context(cafile=str(cert))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             fetch._PinnedHTTPSHandler(context=client_ctx))
        with opener.open(f"https://video.test:{srv.server_port}/", timeout=10) as r:
            assert r.read() == b"ok"
        table["other.test"] = [FAKE_PUBLIC]  # same address, wrong name for the certificate
        with pytest.raises(Exception, match="(?i)certificate|hostname"):
            opener.open(f"https://other.test:{srv.server_port}/", timeout=10)
    finally:
        srv.shutdown()
        srv.server_close()
