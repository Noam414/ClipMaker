import json

import pytest

from clipmaker.cli import main
from clipmaker.media import MediaError, probe


def test_probe_tiny(tiny_video):
    info = probe(tiny_video)
    assert (info.width, info.height) == (640, 360)
    assert info.has_audio and info.has_video
    assert abs(info.duration - 3.0) < 0.2
    assert info.fps == pytest.approx(25.0)


def test_probe_missing_file(tmp_path):
    with pytest.raises(MediaError):
        probe(tmp_path / "nope.mp4")


def test_cli_writes_clips_json(tiny_video, tmp_path):
    assert main([str(tiny_video), "--out", str(tmp_path / "out"), "--no-transcribe"]) == 0
    data = json.loads((tmp_path / "out" / "clips.json").read_text())
    assert data["schema_version"] == 1
    assert data["input"]["width"] == 640
    assert isinstance(data["clips"], list)
