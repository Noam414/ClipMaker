"""Runtime model download + cache. Models come from GitHub release assets
(huggingface.co may be blocked), and are never committed to git."""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from clipmaker.config import get_settings

SHERPA_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"

WHISPER_MODELS = ("tiny", "base", "small")


def models_dir() -> Path:
    path = get_settings().cache_dir / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh, length=1 << 20)
    os.replace(tmp, dest)


def silero_vad() -> Path:
    path = models_dir() / "silero_vad.onnx"
    if not path.is_file():
        _download(f"{SHERPA_RELEASE}/silero_vad.onnx", path)
    return path


def whisper(name: str = "base") -> dict[str, Path]:
    """Return paths to int8 encoder/decoder + tokens for a sherpa-onnx Whisper model."""
    if name not in WHISPER_MODELS:
        raise ValueError(f"unknown whisper model {name!r}; choose from {WHISPER_MODELS}")
    folder = models_dir() / f"sherpa-onnx-whisper-{name}"
    files = {
        "encoder": folder / f"{name}-encoder.int8.onnx",
        "decoder": folder / f"{name}-decoder.int8.onnx",
        "tokens": folder / f"{name}-tokens.txt",
    }
    if all(p.is_file() for p in files.values()):
        return files
    wanted = {p.name for p in files.values()}
    with tempfile.TemporaryDirectory(dir=models_dir()) as tmp:
        archive = Path(tmp) / "model.tar.bz2"
        _download(f"{SHERPA_RELEASE}/sherpa-onnx-whisper-{name}.tar.bz2", archive)
        staging = Path(tmp) / "out"
        staging.mkdir()
        with tarfile.open(archive, "r:bz2") as tar:
            for member in tar.getmembers():
                base = Path(member.name).name
                if member.isfile() and base in wanted:
                    src = tar.extractfile(member)
                    with open(staging / base, "wb") as fh:
                        shutil.copyfileobj(src, fh, length=1 << 20)
        missing = wanted - {p.name for p in staging.iterdir()}
        if missing:
            raise RuntimeError(f"model archive missing files: {sorted(missing)}")
        folder.mkdir(parents=True, exist_ok=True)
        for p in staging.iterdir():
            os.replace(p, folder / p.name)
    return files
