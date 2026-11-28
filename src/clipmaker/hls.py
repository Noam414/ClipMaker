"""HLS (`.m3u8`) links, fetched safely and only as much as needed (M5.5, D-024).

ffmpeg never touches the network here. We read the playlists ourselves and download each
segment through `fetch.build_opener()`, so every playlist, segment and init-section URL
(and every redirect) gets the same SSRF checks and address pinning as a direct link
(D-018, D-022). ffmpeg then only joins and cuts local files (`-protocol_whitelist file`).

- `fetch_audio(url, dest)`: only the audio. With a separate audio rendition only its
  segments are fetched; otherwise the lowest-bandwidth variant (audio is the same, the
  video is smallest) and the video track is dropped.
- `section_source(url, dir)`: for each selected clip, only the segments of the chosen
  video variant (closest to 1080p) that overlap the clip ±pad, cut frame-accurately.
- `download(url, dest)`: the whole video, for callers that want a local file.

Supported: VOD playlists (`#EXT-X-ENDLIST`), MPEG-TS or fMP4 segments (`#EXT-X-MAP`),
`#EXT-X-BYTERANGE`. Refused with a clear message: live streams, encrypted streams.
"""
from __future__ import annotations

import logging
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from clipmaker import fetch
from clipmaker.fetch import FetchError

log = logging.getLogger(__name__)

MAX_PLAYLIST_BYTES = 2 * 1024 ** 2
MAX_SEGMENTS = 20_000
TARGET_HEIGHT = 1080
SECTION_PAD_S = 2.0
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


@dataclass
class Segment:
    uri: str
    start: float
    duration: float
    byterange: tuple[int, int] | None = None  # (offset, length)


@dataclass
class MediaPlaylist:
    segments: list[Segment] = field(default_factory=list)
    init: Segment | None = None

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)


@dataclass
class Variant:
    uri: str
    bandwidth: int = 0
    height: int = 0
    audio_group: str | None = None


def is_hls(url: str) -> bool:
    return urllib.parse.urlsplit(str(url)).path.lower().endswith((".m3u8", ".m3u"))


def _attrs(text: str) -> dict[str, str]:
    return {k: v.strip('"') for k, v in _ATTR.findall(text)}


def _open(url: str, opener, byterange: tuple[int, int] | None = None):
    fetch.check_url(url)
    headers = {"User-Agent": fetch.USER_AGENT}
    if byterange:
        headers["Range"] = f"bytes={byterange[0]}-{byterange[0] + byterange[1] - 1}"
    try:
        return opener.open(urllib.request.Request(url, headers=headers), timeout=fetch.TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        raise FetchError(f"The server answered {exc.code} {exc.reason}.") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FetchError(f"Couldn't download the link: {getattr(exc, 'reason', exc)}.") from exc


def _get_playlist(url: str, opener) -> tuple[str, str]:
    """(text, final url after redirects) of a playlist, size-capped."""
    with _open(url, opener) as resp:
        data = resp.read(MAX_PLAYLIST_BYTES + 1)
        final = resp.geturl() or url
    if len(data) > MAX_PLAYLIST_BYTES:
        raise FetchError("The playlist is too big.")
    text = data.decode("utf-8", "replace").lstrip("﻿")
    if not text.startswith("#EXTM3U"):
        raise FetchError("The link isn't an HLS playlist.")
    return text, final


def _lines(text: str):
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            yield line


def parse_master(text: str, base: str) -> tuple[list[Variant], dict[str, str]]:
    """Variants and {audio group id: audio rendition uri} of a master playlist."""
    variants, audio, pending = [], {}, None
    for line in _lines(text):
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = _attrs(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-MEDIA:"):
            a = _attrs(line.split(":", 1)[1])
            if a.get("TYPE") == "AUDIO" and a.get("URI"):
                gid = a.get("GROUP-ID", "")
                if gid not in audio or a.get("DEFAULT") == "YES":
                    audio[gid] = urllib.parse.urljoin(base, a["URI"])
        elif not line.startswith("#") and pending is not None:
            res = pending.get("RESOLUTION", "")
            height = int(res.split("x")[1]) if re.fullmatch(r"\d+x\d+", res) else 0
            bw = pending.get("BANDWIDTH", "0")
            variants.append(Variant(urllib.parse.urljoin(base, line), int(bw) if bw.isdigit() else 0,
                                    height, pending.get("AUDIO")))
            pending = None
    return variants, audio


def parse_media(text: str, base: str) -> MediaPlaylist:
    pl, t, dur, br, last_end = MediaPlaylist(), 0.0, None, None, {}
    ended = False
    for line in _lines(text):
        if line.startswith("#EXT-X-KEY:"):
            if _attrs(line.split(":", 1)[1]).get("METHOD", "NONE").upper() != "NONE":
                raise FetchError("Encrypted streams aren't supported; upload the file instead.")
        elif line.startswith("#EXT-X-MAP:"):
            a = _attrs(line.split(":", 1)[1])
            rng = _byterange(a.get("BYTERANGE"), 0)
            pl.init = Segment(urllib.parse.urljoin(base, a.get("URI", "")), 0.0, 0.0, rng)
        elif line.startswith("#EXTINF:"):
            dur = float(line.split(":", 1)[1].split(",")[0] or 0)
        elif line.startswith("#EXT-X-BYTERANGE:"):
            br = line.split(":", 1)[1]
        elif line.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif line.startswith("#EXT-X-PLAYLIST-TYPE:") and line.endswith("VOD"):
            ended = True
        elif not line.startswith("#") and dur is not None:
            uri = urllib.parse.urljoin(base, line)
            rng = _byterange(br, last_end.get(uri, 0)) if br else None
            if rng:
                last_end[uri] = rng[0] + rng[1]
            pl.segments.append(Segment(uri, t, dur, rng))
            t += dur
            dur, br = None, None
            if len(pl.segments) > MAX_SEGMENTS:
                raise FetchError("The playlist has too many segments.")
    if not ended:
        raise FetchError("Live streams aren't supported; paste the link after the stream has ended.")
    if not pl.segments:
        raise FetchError("The playlist has no segments.")
    return pl


def _byterange(spec: str | None, default_offset: int) -> tuple[int, int] | None:
    if not spec:
        return None
    length, _, offset = spec.partition("@")
    return (int(offset) if offset else default_offset, int(length))


def pick_variant(variants: list[Variant], target: int = TARGET_HEIGHT, lowest: bool = False) -> Variant:
    if lowest:
        return min(variants, key=lambda v: (v.bandwidth or 1 << 62, v.height or 1 << 30))
    fitting = [v for v in variants if 0 < v.height <= target]
    if fitting:
        return max(fitting, key=lambda v: (v.height, v.bandwidth))
    with_height = [v for v in variants if v.height]
    if with_height:
        return min(with_height, key=lambda v: (v.height, v.bandwidth))
    return max(variants, key=lambda v: v.bandwidth)


@dataclass
class Stream:
    """The playlists to fetch: `video` (muxed with audio unless `audio` is set)."""
    video: MediaPlaylist
    audio: MediaPlaylist | None
    audio_only: MediaPlaylist  # what fetch_audio reads (separate audio, else lowest variant)
    audio_only_has_video: bool

    @property
    def duration(self) -> float:
        return self.video.duration


def resolve(url: str, opener=None, max_seconds: float = fetch.MAX_SECONDS) -> Stream:
    opener = opener or fetch.build_opener()
    text, base = _get_playlist(url, opener)
    if "#EXT-X-STREAM-INF:" not in text:
        media = parse_media(text, base)
        stream = Stream(media, None, media, True)
    else:
        variants, audio = parse_master(text, base)
        if not variants:
            raise FetchError("The playlist has no video streams.")
        best = pick_variant(variants)
        video = parse_media(_get_playlist(best.uri, opener)[0], best.uri)
        audio_uri = audio.get(best.audio_group) if best.audio_group else None
        if audio_uri:
            a = parse_media(_get_playlist(audio_uri, opener)[0], audio_uri)
            stream = Stream(video, a, a, False)
        else:
            low = pick_variant(variants, lowest=True)
            low_pl = video if low.uri == best.uri else parse_media(_get_playlist(low.uri, opener)[0], low.uri)
            stream = Stream(video, None, low_pl, True)
    if stream.duration > max_seconds:
        raise FetchError(f"The video is too long (limit {int(max_seconds // 60)} minutes).")
    return stream


class _Budget:
    def __init__(self, max_bytes: int):
        self.max_bytes, self.used = max_bytes, 0

    def add(self, n: int) -> None:
        self.used += n
        if self.used > self.max_bytes:
            raise FetchError(f"The file is too big (limit {self.max_bytes // 1024 ** 2} MB).")


def _download_segments(pl: MediaPlaylist, segments: list[Segment], dest: Path, opener,
                       budget: _Budget, progress=None) -> Path:
    """Concatenate the init section (if any) and `segments` into `dest`."""
    parts = ([pl.init] if pl.init else []) + segments
    with open(dest, "wb") as fh:
        for i, seg in enumerate(parts):
            with _open(seg.uri, opener, seg.byterange) as resp:
                if seg.byterange and resp.status != 206:
                    raise FetchError("The server doesn't support byte ranges this playlist needs.")
                while chunk := resp.read(1 << 20):
                    budget.add(len(chunk))
                    fh.write(chunk)
            if progress:
                progress((i + 1) / len(parts))
    return dest


def _ffmpeg(args: list[str]) -> None:
    from clipmaker.media import require_tool

    cmd = [require_tool("ffmpeg"), "-v", "error", "-y", "-protocol_whitelist", "file", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=fetch.TIMEOUT_S * 20)
    if proc.returncode != 0:
        log.warning("ffmpeg on HLS segments failed (exit %s): %s", proc.returncode, proc.stderr[-1500:])
        raise FetchError("The stream's segments aren't media ffmpeg can read.")


def _ext(pl: MediaPlaylist) -> str:
    return ".mp4" if pl.init else ".ts"


def fetch_audio(url: str, dest, max_bytes: int = fetch.MAX_BYTES,
                max_seconds: float = fetch.MAX_SECONDS, progress=None) -> Path:
    """Save only the audio of an HLS link to `dest` (.m4a)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    opener = fetch.build_opener()
    stream = resolve(url, opener, max_seconds)
    pl = stream.audio_only
    raw = dest.with_name(dest.stem + ".segments" + _ext(pl))
    tmp = dest.with_name(dest.stem + ".part" + dest.suffix)
    try:
        _download_segments(pl, pl.segments, raw, opener, _Budget(max_bytes), progress)
        _ffmpeg(["-i", str(raw), "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "160k", str(tmp)])
        tmp.replace(dest)
    finally:
        raw.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    return dest


def _start_time(path: Path) -> float:
    from clipmaker.media import require_tool

    out = subprocess.run([require_tool("ffprobe"), "-v", "error", "-show_entries", "format=start_time",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60).stdout
    try:
        return float(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


ORIGIN_PROBE_BYTES = 256 * 1024


def _origin(pl: MediaPlaylist, opener, budget: _Budget, path: Path) -> float:
    """The timestamp at which the playlist's timeline starts: the start time of its first
    segment. Segments carry absolute timestamps (MPEG-TS usually starts at 1.4 s, others
    anywhere), so a section's seek must be relative to this, not to EXTINF sums alone.
    Only the head of the first segment is read."""
    try:
        with open(path, "wb") as fh:
            for seg in ([pl.init] if pl.init else []) + [pl.segments[0]]:
                with _open(seg.uri, opener, seg.byterange) as resp:
                    limit = None if seg is pl.init else ORIGIN_PROBE_BYTES
                    data = resp.read() if limit is None else resp.read(limit)
                budget.add(len(data))
                fh.write(data)
        return _start_time(path)
    finally:
        path.unlink(missing_ok=True)


def _overlapping(pl: MediaPlaylist, a: float, b: float) -> list[Segment]:
    return [s for s in pl.segments if s.start < b and s.start + s.duration > a]


def fetch_section(url: str, start: float, end: float, dest, stream: Stream | None = None,
                  max_bytes: int = fetch.MAX_BYTES) -> Path:
    """Save only [start, end) of an HLS link, video + audio, to `dest` (.mp4), with t=0
    exactly at `start` (re-encoded, like relay.fetch_section and D-020)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    opener = fetch.build_opener()
    stream = stream or resolve(url, opener)
    budget = _Budget(max_bytes)
    segs = _overlapping(stream.video, start, end)
    if not segs:
        raise FetchError("The clip is outside the stream.")
    raw_v = dest.with_name(dest.stem + ".v" + _ext(stream.video))
    files = [raw_v]
    tmp = dest.with_name(dest.stem + ".part.mp4")
    try:
        origin = _origin(stream.video, opener, budget, dest.with_name(dest.stem + ".o" + _ext(stream.video)))
        _download_segments(stream.video, segs, raw_v, opener, budget)
        # Each input's t=0 is its own first timestamp; v0 is where raw_v starts in the
        # source. The seek is an output option (decode from the segment start and drop
        # frames): input seeking in concatenated MPEG-TS landed ~0.5 s late in tests.
        v0 = _start_time(raw_v) - origin
        inputs = ["-i", str(raw_v)]
        maps = ["-map", "0:v:0", "-map", "0:a:0?"]
        if stream.audio:
            asegs = _overlapping(stream.audio, start, end)
            raw_a = dest.with_name(dest.stem + ".a" + _ext(stream.audio))
            files.append(raw_a)
            a_origin = _origin(stream.audio, opener, budget, dest.with_name(dest.stem + ".oa" + _ext(stream.audio)))
            _download_segments(stream.audio, asegs, raw_a, opener, budget)
            a0 = _start_time(raw_a) - a_origin
            inputs += ["-itsoffset", f"{a0 - v0:.3f}", "-i", str(raw_a)]
            maps = ["-map", "0:v:0", "-map", "1:a:0"]
        _ffmpeg([*inputs, "-ss", f"{max(0.0, start - v0):.3f}", "-t", f"{end - start:.3f}", *maps,
                 "-c:v", "libx264", "-preset", "superfast", "-crf", "18", "-c:a", "aac", "-b:a", "160k",
                 str(tmp)])
        tmp.replace(dest)
    finally:
        for f in files + [tmp]:
            f.unlink(missing_ok=True)
    log.info("HLS section %.1f-%.1f s: %d segments, %d bytes", start, end, len(segs), budget.used)
    return dest


def section_source(url: str, dest_dir: Path, pad: float = SECTION_PAD_S):
    """A `video_source` for run_pipeline (sections mode, like platforms.section_source)."""
    dest_dir = Path(dest_dir)
    stream = resolve(url)

    def source(clip: dict) -> tuple[Path, float]:
        a = max(0.0, float(clip["start"]) - pad)
        b = min(float(clip["end"]) + pad, stream.duration)
        return fetch_section(url, a, b, dest_dir / f"{clip['id']}_source.mp4", stream), a

    return source


def download(url: str, dest, max_bytes: int = fetch.MAX_BYTES,
             max_seconds: float = fetch.MAX_SECONDS, progress=None) -> Path:
    """The whole HLS video as one local .mp4 (stream copy)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    opener = fetch.build_opener()
    stream = resolve(url, opener, max_seconds)
    budget = _Budget(max_bytes)
    raw_v = dest.with_name(dest.stem + ".v" + _ext(stream.video))
    files, inputs = [raw_v], ["-i", str(raw_v)]
    tmp = dest.with_name(dest.stem + ".part.mp4")
    try:
        _download_segments(stream.video, stream.video.segments, raw_v, opener, budget, progress)
        maps = ["-map", "0:v:0", "-map", "0:a:0?"]
        if stream.audio:
            raw_a = dest.with_name(dest.stem + ".a" + _ext(stream.audio))
            files.append(raw_a)
            _download_segments(stream.audio, stream.audio.segments, raw_a, opener, budget)
            inputs += ["-i", str(raw_a)]
            maps = ["-map", "0:v:0", "-map", "1:a:0"]
        _ffmpeg([*inputs, *maps, "-c", "copy", str(tmp)])
        tmp.replace(dest)
    finally:
        for f in files + [tmp]:
            f.unlink(missing_ok=True)
    return dest
