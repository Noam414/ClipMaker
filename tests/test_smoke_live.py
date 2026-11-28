"""scripts/smoke_live.py against a local server (M7): the live-site check works before the site exists."""
import importlib.util
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from clipmaker.config import Settings
from clipmaker.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("smoke_live", ROOT / "scripts" / "smoke_live.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def _runner(src, out, progress=None, on_clip=None, **opts):
    """A fast stand-in for the pipeline: one real 9:16 clip from a hand-made transcript."""
    from clipmaker.media import probe
    from clipmaker.pipeline import render_clip_entry

    text = "here is the one thing to check before you ship"
    words = [{"word": w, "start": 0.5 + i * 0.5, "end": 0.9 + i * 0.5} for i, w in enumerate(text.split())]
    seg = {"start": 0.5, "end": words[-1]["end"], "text": text, "words": words}
    clip = {"id": "clip_01", "start": 0.0, "end": 5.5, "text": text, "score": 0.7}
    Path(out).mkdir(parents=True, exist_ok=True)
    render_clip_entry(clip, src, 0.0, probe(src), words, [seg], [], out, None, {"reframe": False, "zoom": False})
    result = {"transcript": {"segments": [seg]}, "clips": [clip]}
    import json

    (Path(out) / "clips.json").write_text(json.dumps(result))
    return result


@pytest.fixture()
def server(tmp_path, monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    monkeypatch.setenv("CLIPMAKER_PRIVATE", "1")  # the smoke script must keep its visitor cookie
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = Settings(database_url=f"sqlite:///{(tmp_path / 's.db').as_posix()}", data_dir=tmp_path / "data",
                        cache_dir=tmp_path / "cache", host="127.0.0.1", port=port, worker=True)
    app = create_app(settings, runner=_runner)
    app.state.worker.poll = 0.2
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(10)


@pytest.fixture()
def video(tmp_path):
    out = tmp_path / "talk.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


def test_smoke_passes_with_edit(server, video):
    lines = []
    summary = smoke.run(server, video, timeout=120, edit=True, say=lines.append)
    assert summary["clips"] == 1 and summary["edited"] is True
    assert any("1080x1920, audio ok" in ln for ln in lines)
    assert smoke.main([server, "--video", str(video), "--timeout", "120"]) == 0


def test_smoke_reports_failures(server, tmp_path, capsys):
    assert smoke.main([server, "--video", str(tmp_path / "missing.mp4")]) == 1
    assert "no such file" in capsys.readouterr().err
    assert smoke.main(["http://127.0.0.1:9", "--video", str(tmp_path / "x.mp4")]) == 1
    assert "cannot reach" in capsys.readouterr().err
