"""HLS links (M5.5, D-024): playlists and segments fetched by us through the checked
opener; audio only, then only the selected sections, frame-accurate."""
import shutil
import subprocess

import pytest

from clipmaker import fetch, hls
from clipmaker.media import probe

from conftest import FIXTURES
from rangeserver import serve

VIDEO = FIXTURES / "podcast_landscape.mp4"


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def streams(tmp_path_factory):
    """Two HLS packagings of the podcast fixture:
    muxed/master.m3u8 — two variants (1080p, 360p), MPEG-TS, audio muxed into each;
    split/master.m3u8 — one 720p video variant + a separate audio rendition, fMP4."""
    if not VIDEO.is_file() or not shutil.which("ffmpeg"):
        pytest.skip("fixtures missing; run python3 scripts/make_test_videos.py")
    root = tmp_path_factory.mktemp("hls")
    (root / "muxed").mkdir()
    _ffmpeg("-i", str(VIDEO), "-filter_complex", "[0:v]split=2[a][b];[b]scale=640:360[s]",
            "-map", "[a]", "-map", "0:a", "-map", "[s]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-b:v:0", "3M", "-b:v:1", "200k",
            "-c:a", "aac", "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod",
            "-var_stream_map", "v:0,a:0 v:1,a:1", "-master_pl_name", "master.m3u8",
            "-hls_segment_filename", str(root / "muxed" / "v%v_%03d.ts"),
            str(root / "muxed" / "v%v.m3u8"))
    (root / "split").mkdir()
    _ffmpeg("-i", str(VIDEO), "-map", "0:v", "-map", "0:a", "-vf", "scale=1280:720",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-c:a", "aac",
            "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod",
            "-hls_segment_type", "fmp4", "-var_stream_map", "v:0,agroup:aud a:0,agroup:aud,default:yes",
            "-master_pl_name", "master.m3u8", "-hls_fmp4_init_filename", "init_%v.mp4",
            "-hls_segment_filename", str(root / "split" / "s%v_%03d.m4s"),
            str(root / "split" / "p%v.m3u8"))
    return root


@pytest.fixture()
def site(streams, monkeypatch):
    monkeypatch.setenv("CLIPMAKER_ALLOW_PRIVATE_URLS", "1")
    with serve(streams) as (url, stats):
        yield url, stats


def _psnr(a, b, frames=10):
    out = subprocess.run(["ffmpeg", "-i", str(a), "-i", str(b), "-frames:v", str(frames),
                          "-lavfi", "[0:v][1:v]psnr", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    value = out.rsplit("average:", 1)[1].split()[0]
    return float("inf") if value == "inf" else float(value)


def test_parse_master_and_pick_variant():
    text = ("#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"a\",NAME=\"en\",DEFAULT=YES,URI=\"audio/en.m3u8\"\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=3840x2160,AUDIO=\"a\"\nv4k.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,AUDIO=\"a\"\nv1080.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=640x360,AUDIO=\"a\"\nv360.m3u8\n")
    variants, audio = hls.parse_master(text, "https://cdn.example/show/master.m3u8")
    assert hls.pick_variant(variants).uri == "https://cdn.example/show/v1080.m3u8"
    assert hls.pick_variant(variants, lowest=True).uri.endswith("v360.m3u8")
    assert audio == {"a": "https://cdn.example/show/audio/en.m3u8"}


def test_parse_media_refuses_live_and_encrypted_and_reads_byteranges():
    base = "https://cdn.example/p.m3u8"
    with pytest.raises(fetch.FetchError, match="Live"):
        hls.parse_media("#EXTM3U\n#EXTINF:4,\na.ts\n", base)
    with pytest.raises(fetch.FetchError, match="Encrypted"):
        hls.parse_media("#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"k\"\n#EXTINF:4,\na.ts\n#EXT-X-ENDLIST\n", base)
    pl = hls.parse_media("#EXTM3U\n#EXT-X-MAP:URI=\"all.mp4\",BYTERANGE=\"700@0\"\n"
                         "#EXTINF:4.0,\n#EXT-X-BYTERANGE:1000@700\nall.mp4\n"
                         "#EXTINF:3.5,\n#EXT-X-BYTERANGE:900\nall.mp4\n#EXT-X-ENDLIST\n", base)
    assert pl.init.byterange == (0, 700)
    assert [(s.start, s.byterange) for s in pl.segments] == [(0.0, (700, 1000)), (4.0, (1700, 900))]
    assert pl.duration == 7.5


@pytest.mark.parametrize("kind", ["muxed", "split"])
def test_audio_only_fetches_a_fraction(site, streams, tmp_path, kind):
    url, stats = site
    out = hls.fetch_audio(f"{url}/{kind}/master.m3u8", tmp_path / "a.m4a")
    info = probe(out)
    assert info.has_audio and not info.has_video
    assert abs(info.duration - probe(VIDEO).duration) < 0.5
    total = sum(p.stat().st_size for p in (streams / kind).iterdir())
    # muxed: the 360p variant only; split: the audio rendition only
    assert stats["bytes"] < 0.35 * total, (stats["bytes"], total)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.m4a"]


@pytest.mark.parametrize("kind,height", [("muxed", 1080), ("split", 720)])
def test_section_is_frame_accurate_and_fetches_only_its_segments(site, streams, tmp_path, kind, height):
    url, stats = site
    out = hls.fetch_section(f"{url}/{kind}/master.m3u8", 21.3, 27.1, tmp_path / "s.mp4")
    info = probe(out)
    assert info.has_video and info.has_audio and info.height == height
    assert abs(info.duration - 5.8) < 0.15
    ref = tmp_path / "ref.mp4"
    _ffmpeg("-ss", "21.3", "-i", str(VIDEO), "-t", "1", "-vf", f"scale=-2:{height}",
            "-c:v", "libx264", "-preset", "ultrafast", str(ref))
    assert _psnr(out, ref) > 32  # 0.5 s late measured 25 dB; aligned 35+
    segs = [p for p in stats["paths"] if p.endswith((".ts", ".m4s"))]
    assert 2 <= len(segs) <= 6, segs  # 21.3-27.1 s spans segments 5-6 (+audio for split)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ref.mp4", "s.mp4"]


def test_full_download_and_section_source(site, tmp_path):
    url, _ = site
    out = hls.download(f"{url}/split/master.m3u8", tmp_path / "full.mp4")
    info = probe(out)
    assert info.has_video and info.has_audio and abs(info.duration - probe(VIDEO).duration) < 0.5
    src = hls.section_source(f"{url}/muxed/master.m3u8", tmp_path / "sec")
    path, off = src({"id": "c1", "start": 1.0, "end": 9.0})
    assert off == 0.0 and abs(probe(path).duration - 11.0) < 0.15


def test_limits_and_ssrf(site, tmp_path, monkeypatch):
    url, _ = site
    with pytest.raises(fetch.FetchError, match="too long"):
        hls.fetch_audio(f"{url}/muxed/master.m3u8", tmp_path / "a.m4a", max_seconds=30)
    with pytest.raises(fetch.FetchError, match="too big"):
        hls.fetch_audio(f"{url}/muxed/master.m3u8", tmp_path / "a.m4a", max_bytes=100_000)
    with pytest.raises(fetch.FetchError, match="isn't an HLS playlist"):
        hls.fetch_audio(f"{url}/muxed/v0_000.ts", tmp_path / "a.m4a")
    monkeypatch.delenv("CLIPMAKER_ALLOW_PRIVATE_URLS")
    with pytest.raises(fetch.FetchError, match="private"):
        hls.fetch_audio(f"{url}/muxed/master.m3u8", tmp_path / "a.m4a")


def test_segment_on_a_private_host_is_refused(tmp_path, monkeypatch):
    """A public playlist whose segment points at 127.0.0.1 never reaches it."""
    pl = ("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nhttp://127.0.0.1:9/secret.ts\n#EXT-X-ENDLIST\n")
    opened = []

    class FakeResp:
        status = 200

        def __init__(self, data):
            self.data = data

        def read(self, n=-1):
            d, self.data = self.data, b""
            return d

        def geturl(self):
            return "https://cdn.example/p.m3u8"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeOpener:
        def open(self, req, timeout=None):
            opened.append(req.full_url)
            return FakeResp(pl.encode())

    monkeypatch.setattr(fetch, "build_opener", lambda: FakeOpener())
    monkeypatch.setattr(fetch, "checked_addresses",
                        lambda host, port: fetch._check_ip("93.184.216.34" if host == "cdn.example"
                                                           else "127.0.0.1"))
    with pytest.raises(fetch.FetchError, match="private"):
        hls.fetch_audio("https://cdn.example/p.m3u8", tmp_path / "a.m4a")
    assert opened == ["https://cdn.example/p.m3u8"]


def test_web_job_with_an_hls_link_fetches_audio_then_sections(site, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import clipmaker.jobs as jobs
    from clipmaker.config import Settings
    from clipmaker.jobs import project_dir
    from clipmaker.web.app import create_app

    url, stats = site
    link = f"{url}/muxed/master.m3u8"
    seen, stages = {}, []

    def runner(src, out, progress=None, on_clip=None, video_source=None, **opts):
        seen["src"] = probe(src)
        path, off = video_source({"id": "clip_01", "start": 30.0, "end": 36.0})
        seen["section"], seen["off"] = probe(path), off
        return {"clips": []}

    settings = Settings(database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}", data_dir=tmp_path,
                        cache_dir=tmp_path / "cache", host="127.0.0.1", port=0, worker=False)
    app = create_app(settings, runner=runner)
    r = TestClient(app).post("/api/projects/link", json={"url": link})
    assert r.status_code == 201, r.text
    orig = jobs._set_progress
    monkeypatch.setattr(jobs, "_set_progress", lambda e, j, st, f: (stages.append(st), orig(e, j, st, f)))
    assert app.state.worker.run_once() == "done"
    assert seen["src"].has_audio and not seen["src"].has_video
    assert seen["off"] == 28.0 and abs(seen["section"].duration - 10.0) < 0.15
    assert "download audio" in stages
    assert (project_dir(tmp_path, r.json()["project_id"]) / "download" / "audio.m4a").is_file()
    v0 = sorted(p for p in stats["paths"] if p.startswith("/muxed/v0_"))
    # 1080p: the head of segment 0 (timeline origin) and segments 7-9 (28-38 s) only
    assert v0 == ["/muxed/v0_000.ts", "/muxed/v0_007.ts", "/muxed/v0_008.ts", "/muxed/v0_009.ts"], v0
