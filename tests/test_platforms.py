"""Platform-link fetcher (yt-dlp) against a local fake site; youtube.com is blocked in the VM."""
import http.server
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from clipmaker.fetch import FetchError
from clipmaker.platforms import (YtdlpConfig, diagnose, fetch_audio, friendly_error, platform_of,
                                 ytdlp_args)

pytest.importorskip("yt_dlp")


@pytest.mark.parametrize("url,name", [
    ("https://www.youtube.com/watch?v=abc", "youtube"), ("https://youtu.be/abc", "youtube"),
    ("https://m.youtube.com/shorts/abc", "youtube"), ("https://www.twitch.tv/videos/123", "twitch"),
    ("https://clips.twitch.tv/Name", "twitch"), ("https://notyoutube.com/watch", None),
    ("https://youtube.com.evil.example/watch", None), ("https://cdn.example.com/a.mp4", None),
])
def test_platform_of(url, name):
    assert platform_of(url) == name


def test_config_from_env_and_args(tmp_path):
    env = {"CLIPMAKER_YTDLP_POT_URL": "http://127.0.0.1:4416/", "CLIPMAKER_YTDLP_COOKIES": "/c/cookies.txt",
           "CLIPMAKER_YTDLP_PROXY": "socks5://p:1080", "CLIPMAKER_YTDLP_JS_RUNTIME": "node"}
    cfg = YtdlpConfig.from_env(env)
    args = ytdlp_args(cfg, cookies_copy=tmp_path / "copy.txt")
    joined = " ".join(args)
    assert "--extractor-args youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416" in joined
    assert f"--cookies {tmp_path / 'copy.txt'}" in joined  # the copy, never the owner's file
    assert "--proxy socks5://p:1080" in joined and "--js-runtimes node" in joined
    assert "--use-extractors youtube,twitch" in joined


def test_defaults_add_nothing_optional():
    args = ytdlp_args(YtdlpConfig.from_env({}))
    assert not {"--cookies", "--proxy", "--extractor-args", "--js-runtimes"} & set(args)


def test_installed_ytdlp_knows_every_option():
    help_text = subprocess.run([sys.executable, "-m", "yt_dlp", "--help"], capture_output=True,
                               text=True, check=True).stdout
    cfg = YtdlpConfig(pot_url="http://x", cookies_file=Path("c"), proxy="http://p", js_runtime="node")
    for opt in [a for a in ytdlp_args(cfg) if a.startswith("--")] + ["--match-filters", "--max-filesize"]:
        assert opt in help_text, opt


@pytest.mark.parametrize("stderr,needle", [
    ("ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you’re not a bot. Use --cookies-from-browser "
     "or --cookies for the authentication.", "bot check"),
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot. This helps protect our community.", "bot check"),
    ("ERROR: unable to download webpage: HTTP Error 429: Too Many Requests", "rate-limiting"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "private"),
    ("ERROR: [youtube] x: Video unavailable. This video has been removed by the uploader", "unavailable"),
    ("ERROR: [twitch:stream] somebody: The channel is not currently live", "offline"),
    ("ERROR: something new and strange", "Upload the video file"),
])
def test_friendly_errors(stderr, needle):
    assert needle in friendly_error(stderr)


class _FakeSite(http.server.BaseHTTPRequestHandler):
    """Serves /talk.m4a; also acts as a plain HTTP proxy (absolute-URL requests)."""
    body = b""
    seen: list = []

    def do_GET(self):
        type(self).seen.append({"path": self.path, "cookie": self.headers.get("Cookie", "")})
        if not self.path.endswith("/talk.m4a"):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def fake_site(tmp_path_factory):
    audio = tmp_path_factory.mktemp("site") / "talk.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:a", "aac", str(audio)], check=True)
    _FakeSite.body = audio.read_bytes()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeSite)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _local_config(**kw):
    # the generic extractor is off in production; tests turn it on to reach the fake site
    return YtdlpConfig(extractors=("generic",), **kw)


def test_fetch_audio_with_cookies_and_proxy(fake_site, tmp_path, monkeypatch):
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    cookies = tmp_path / "cookies.txt"
    original = "# Netscape HTTP Cookie File\n127.0.0.1\tFALSE\t/\tFALSE\t2147483647\tSID\towner-session\n"
    cookies.write_text(original)
    _FakeSite.seen.clear()
    out = fetch_audio(f"{fake_site}/talk.m4a", tmp_path / "job",
                      _local_config(cookies_file=cookies, proxy=fake_site))
    assert out.is_file() and out.stat().st_size == len(_FakeSite.body)
    media = [r for r in _FakeSite.seen if r["path"].endswith("/talk.m4a")]
    assert media and all(r["path"].startswith("http://") for r in media)  # went through the proxy
    assert any("SID=owner-session" in r["cookie"] for r in media)  # cookies were sent
    assert cookies.read_text() == original  # the owner's file is untouched
    assert not (tmp_path / "job" / ".cookies.txt").exists()  # the per-run copy is removed


def test_fetch_audio_too_long(fake_site, tmp_path):
    with pytest.raises(FetchError, match="too long"):
        fetch_audio(f"{fake_site}/talk.m4a", tmp_path / "job", _local_config(), max_seconds=1)


def test_missing_cookies_file(tmp_path):
    with pytest.raises(FetchError, match="cookies file"):
        fetch_audio("https://www.youtube.com/watch?v=x", tmp_path, YtdlpConfig(cookies_file=tmp_path / "no.txt"))


def test_platform_extractors_refuse_other_hosts(fake_site, tmp_path):
    # with the production extractor list, a non-platform link is not fetched at all
    _FakeSite.seen.clear()
    with pytest.raises(FetchError):
        fetch_audio(f"{fake_site}/talk.m4a", tmp_path / "job", YtdlpConfig())
    assert _FakeSite.seen == []


def test_diagnose_reports_settings(tmp_path):
    report = diagnose(YtdlpConfig(pot_url="http://127.0.0.1:9", cookies_file=tmp_path / "none.txt"))
    assert report["yt_dlp"] is True and report["pot_server"] is False and report["cookies"] is False


def test_platform_check_cli(monkeypatch, tmp_path, capsys):
    from clipmaker.cli import main
    monkeypatch.setenv("CLIPMAKER_YTDLP_COOKIES", str(tmp_path / "missing.txt"))
    assert main(["platform-check"]) == 1
    assert "missing file" in capsys.readouterr().err
