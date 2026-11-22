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


def _fetch_release_archive(archive: str, folder: Path, files: dict[str, Path]) -> dict[str, Path]:
    """Download `archive` (.tar.bz2) from the sherpa-onnx release and keep only `files`."""
    if all(p.is_file() for p in files.values()):
        return files
    wanted = {p.name for p in files.values()}
    with tempfile.TemporaryDirectory(dir=models_dir()) as tmp:
        path = Path(tmp) / "model.tar.bz2"
        _download(f"{SHERPA_RELEASE}/{archive}", path)
        staging = Path(tmp) / "out"
        staging.mkdir()
        with tarfile.open(path, "r:bz2") as tar:
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
    return _fetch_release_archive(f"sherpa-onnx-whisper-{name}.tar.bz2", folder, files)


# Moonshine (Useful Sensors, MIT licence for the English models): English-only ASR that
# scales its compute with the audio length instead of padding to 30 s like Whisper (D-016).
MOONSHINE_MODELS = ("tiny", "base")


def moonshine(name: str = "base") -> dict[str, Path]:
    """Return paths to the int8 sherpa-onnx Moonshine model files (English only)."""
    if name not in MOONSHINE_MODELS:
        raise ValueError(f"unknown moonshine model {name!r}; choose from {MOONSHINE_MODELS}")
    folder = models_dir() / f"sherpa-onnx-moonshine-{name}-en-int8"
    files = {
        "preprocessor": folder / "preprocess.onnx",
        "encoder": folder / "encode.int8.onnx",
        "uncached_decoder": folder / "uncached_decode.int8.onnx",
        "cached_decoder": folder / "cached_decode.int8.onnx",
        "tokens": folder / "tokens.txt",
    }
    return _fetch_release_archive(f"sherpa-onnx-moonshine-{name}-en-int8.tar.bz2", folder, files)


# YuNet face detector (MIT), from opencv_zoo. The repo stores it with git LFS, so the
# file is served by media.githubusercontent.com; pinned to a commit-independent sha256.
YUNET_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def yunet() -> Path:
    import hashlib

    path = models_dir() / "face_detection_yunet_2023mar.onnx"
    if not path.is_file():
        _download(YUNET_URL, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != YUNET_SHA256:
            path.unlink()
            raise RuntimeError(f"YuNet checksum mismatch: {digest}")
    return path
