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


def test_redirect_to_internal_address_is_blocked(server, tmp_path, monkeypatch):
    base, _ = server
    monkeypatch.delenv("CLIPMAKER_ALLOW_PRIVATE_URLS", raising=False)
    # "localhost" passes the check as if it were public; the redirect target 127.0.0.1 must not
    monkeypatch.setattr(fetch, "_resolve", _fake_dns({"localhost": "93.184.216.34", "127.0.0.1": "127.0.0.1"}))
    with pytest.raises(FetchError, match="private or internal"):
        download(f"{base}/to-internal", tmp_path / "v.mp4")
    assert not list(tmp_path.iterdir())


def test_cli_reports_blocked_link(tmp_path, monkeypatch, capsys):
    from clipmaker.cli import main

    monkeypatch.delenv("CLIPMAKER_ALLOW_PRIVATE_URLS", raising=False)
    assert main(["http://127.0.0.1:9/video.mp4", "--out", str(tmp_path / "out")]) == 2
    assert "private or internal" in capsys.readouterr().err
