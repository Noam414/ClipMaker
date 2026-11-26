import threading

from clipmaker import models


def test_parallel_downloads_of_one_model_dont_clash(tmp_path):
    """Render workers on a cold cache fetch the face model at the same time (seen 2026-11-26)."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"m" * 3_000_000)
    dest = tmp_path / "cache" / "model.onnx"
    dest.parent.mkdir()
    errors = []

    def run():
        try:
            models._download(src.as_uri(), dest)
        except Exception as exc:  # pragma: no cover - the failure being tested
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and dest.read_bytes() == src.read_bytes()
    assert [p.name for p in dest.parent.iterdir()] == ["model.onnx"]
