"""Sections mode (M5.5): audio first, then only the selected clips' video, via yt-dlp
`--download-sections` against a local HTTP server (platform sites are blocked in the VM)."""
import functools
import http.server
import subprocess
import threading

import pytest

from clipmaker.media import probe
from clipmaker.platforms import YtdlpConfig, fetch_section, section_source

from conftest import FIXTURES

pytest.importorskip("yt_dlp")
VIDEO = FIXTURES / "podcast_landscape.mp4"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    if not VIDEO.is_file():
        pytest.skip("fixtures missing; run python3 scripts/make_test_videos.py")
    handler = functools.partial(_Quiet, directory=str(FIXTURES))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


# production allows only youtube,twitch; --verbose puts yt-dlp's ffmpeg command and output
# into the captured log if a download fails (CI: ffmpeg exit 183 on 2026-11-24, not seen locally)
LOCAL = YtdlpConfig(extractors=("generic",), extra_args=["--verbose"])


def _psnr(a, b) -> float:
    out = subprocess.run(["ffmpeg", "-i", str(a), "-i", str(b), "-frames:v", "10",
                          "-lavfi", "[0:v][1:v]psnr", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    value = out.rsplit("average:", 1)[1].split()[0]
    return 99.0 if value == "inf" else float(value)


def test_fetch_section_is_frame_accurate(site, tmp_path):
    out = fetch_section(f"{site}/{VIDEO.name}", 12.3, 18.1, tmp_path / "s.mp4", LOCAL)
    info = probe(out)
    assert info.has_video and info.has_audio
    assert abs(info.duration - 5.8) < 0.1
    ref = tmp_path / "ref.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "12.3", "-i", str(VIDEO), "-t", "1",
                    "-c:v", "libx264", "-preset", "ultrafast", str(ref)], check=True)
    assert _psnr(out, ref) > 40  # same frames, so t=0 of the section is 12.3 s in the source


def test_pipeline_sections_mode_matches_full_video(site, tmp_path):
    from clipmaker.pipeline import run_pipeline

    audio = tmp_path / "audio.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(VIDEO), "-vn", "-c:a", "copy", str(audio)],
                   check=True)
    duration = probe(audio).duration
    source = section_source(f"{site}/{VIDEO.name}", tmp_path / "sections", duration, LOCAL)
    opts = dict(language="en", max_clips=1)
    sec = run_pipeline(audio, tmp_path / "sec", video_source=source, **opts)
    full = run_pipeline(VIDEO, tmp_path / "full", **opts)

    assert sec["scene_cuts_scope"] == "sections" and sec["video_source"] == "sections"
    (c,), (f,) = sec["clips"], full["clips"]
    assert (c["start"], c["end"]) == (f["start"], f["end"])
    assert c["source_offset"] == round(max(0.0, c["start"] - 2.0), 3)
    assert "fetch_seconds" in c["timings"]
    out = probe(tmp_path / "sec" / c["file"])
    assert (out.width, out.height) == (1080, 1920) and out.has_audio
    assert abs(out.duration - c["output_duration"]) < 0.25
    # the camera path is in source time, like the full-video run
    assert c["start"] - 0.05 <= c["crop_track"][0][0] <= c["end"]
    mean = lambda t: sum(r[1] for r in t) / len(t)
    assert abs(mean(c["crop_track"]) - mean(f["crop_track"])) < 40
