"""Direct links through the loopback relay (M5.5): audio only, then only a section, with
every upstream request going through the checked, pinned opener."""
import subprocess

import pytest

from clipmaker import fetch, relay
from clipmaker.media import probe

from conftest import FIXTURES
from rangeserver import serve

VIDEO = FIXTURES / "podcast_landscape.mp4"


@pytest.fixture()
def site(monkeypatch):
    if not VIDEO.is_file():
        pytest.skip("fixtures missing; run python3 scripts/make_test_videos.py")
    monkeypatch.setenv("CLIPMAKER_ALLOW_PRIVATE_URLS", "1")
    with serve(FIXTURES) as (url, stats):
        yield f"{url}/{VIDEO.name}", stats


def test_audio_only(site, tmp_path):
    url, _ = site
    out = relay.fetch_audio(url, tmp_path / "a.m4a")
    info = probe(out)
    assert info.has_audio and not info.has_video
    assert abs(info.duration - probe(VIDEO).duration) < 0.5
    with pytest.raises(fetch.FetchError, match="too long"):
        relay.fetch_audio(url, tmp_path / "b.m4a", max_seconds=5)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.m4a"]


def test_section_fetches_only_part_and_is_frame_accurate(site, tmp_path):
    url, stats = site
    out = relay.fetch_section(url, 12.3, 18.1, tmp_path / "s.mp4")
    info = probe(out)
    assert info.has_video and info.has_audio and abs(info.duration - 5.8) < 0.1
    ref = tmp_path / "ref.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "12.3", "-i", str(VIDEO), "-t", "1",
                    "-c:v", "libx264", "-preset", "ultrafast", str(ref)], check=True)
    psnr = subprocess.run(["ffmpeg", "-i", str(out), "-i", str(ref), "-frames:v", "10",
                           "-lavfi", "[0:v][1:v]psnr", "-f", "null", "-"],
                          capture_output=True, text=True).stderr.rsplit("average:", 1)[1].split()[0]
    assert psnr == "inf" or float(psnr) > 40


def test_section_of_a_long_video_fetches_a_fraction(tmp_path, monkeypatch):
    long = FIXTURES / "long_talk.mp4"
    if not long.is_file():
        pytest.skip("long_talk fixture not generated (--no-long)")
    monkeypatch.setenv("CLIPMAKER_ALLOW_PRIVATE_URLS", "1")
    with serve(FIXTURES) as (base, stats):
        out = relay.fetch_section(f"{base}/{long.name}", 300.0, 306.0, tmp_path / "s.mp4")
    assert abs(probe(out).duration - 6.0) < 0.1
    # ffmpeg seeked with a Range request into the middle instead of reading from the start;
    # the bytes counted on loopback are dominated by socket buffers (a few MB), so on a real
    # 60-min video the fraction is far smaller than on this 14 MB fixture
    size = long.stat().st_size
    starts = [int(r.split("=")[1].split("-")[0] or 0) for r in stats["ranges"]]
    assert any(0.3 * size < s < 0.7 * size for s in starts), stats
    assert stats["bytes"] < size, stats


def test_byte_budget(site, tmp_path):
    url, _ = site
    with pytest.raises(fetch.FetchError, match="too big"):
        relay.fetch_audio(url, tmp_path / "a.m4a", max_bytes=100_000)
    assert not list(tmp_path.iterdir())


def test_relay_serves_only_its_path_and_blocks_private_upstream(monkeypatch, site, tmp_path):
    import urllib.error
    import urllib.request

    url, _ = site
    with relay.relay(url) as r:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with pytest.raises(urllib.error.HTTPError) as e:
            opener.open(r.local_url.rsplit("/", 2)[0] + "/other")
        assert e.value.code == 404
    monkeypatch.delenv("CLIPMAKER_ALLOW_PRIVATE_URLS")
    with pytest.raises(fetch.FetchError, match="private or internal"):
        relay.fetch_audio(url, tmp_path / "never.m4a")
    assert not list(tmp_path.iterdir())
