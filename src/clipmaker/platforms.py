"""Platform links (YouTube, Twitch) through yt-dlp, with optional anti-bot settings (D-019).

YouTube often answers server/datacenter IPs with "Sign in to confirm you're not a bot".
Three optional, independent settings help yt-dlp past that; all come from the
environment and none is needed for the code to run:

- `CLIPMAKER_YTDLP_POT_URL`: base URL of a running bgutil PO-token HTTP provider
  (e.g. `http://127.0.0.1:4416`, the Docker image `brainicism/bgutil-ytdlp-pot-provider`).
  It needs the yt-dlp plugin `bgutil-ytdlp-pot-provider` installed next to yt-dlp.
- `CLIPMAKER_YTDLP_COOKIES`: path to a Netscape-format cookies.txt exported from an
  account the owner controls. It is copied per run, because yt-dlp rewrites the file.
- `CLIPMAKER_YTDLP_PROXY`: a proxy URL (`http://user:pass@host:port`, `socks5://…`).
- `CLIPMAKER_YTDLP_JS_RUNTIME`: JavaScript runtime yt-dlp uses for YouTube's player
  challenges (`deno`, yt-dlp's default, or `node`).

yt-dlp runs as a separate process (`python -m yt_dlp`): the GPL-3.0 PO-token plugin is
loaded by yt-dlp only, never imported by ClipMaker. Only the YouTube and Twitch
extractors are enabled, so a platform link can't be used to make the server fetch
arbitrary hosts (the generic extractor stays off unless a caller asks for it in tests).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from clipmaker.fetch import MAX_BYTES, MAX_SECONDS, FetchError

PLATFORM_HOSTS = {
    "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    "twitch": ("twitch.tv",),
}
EXTRACTORS = ("youtube", "twitch")
POT_PLUGIN_MODULE = "yt_dlp_plugins.extractor.getpot_bgutil_http"
TIMEOUT_S = 30 * 60

# yt-dlp error text -> message that is safe and useful to show users.
_ERRORS = (
    (("confirm you're not a bot", "confirm you’re not a bot", "sign in to confirm"),
     "YouTube is blocking downloads from this server right now (bot check). "
     "Try again later, or upload the video file instead."),
    (("http error 429", "too many requests"),
     "The site is rate-limiting this server. Try again in a few minutes, or upload the file instead."),
    (("private video", "members-only", "join this channel"),
     "This video is private or members-only, so it can't be imported. Upload the file instead."),
    (("sign in to confirm your age", "age-restricted", "inappropriate for some users"),
     "This video is age-restricted, so it can't be imported without signing in. Upload the file instead."),
    (("video unavailable", "this video has been removed", "does not exist"),
     "The video is unavailable (removed, region-locked or a wrong link)."),
    (("is offline", "the channel is not currently live"),
     "The stream is offline. Use a link to a past broadcast (VOD) or a clip."),
    (("unable to connect to proxy", "proxyerror", "tunnel connection failed"),
     "The download proxy isn't reachable. Check CLIPMAKER_YTDLP_PROXY."),
)


def platform_of(url: str) -> str | None:
    """'youtube' or 'twitch' for links to those sites, else None."""
    host = (urllib.parse.urlsplit(str(url)).hostname or "").lower()
    for name, domains in PLATFORM_HOSTS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    return None


@dataclass
class YtdlpConfig:
    pot_url: str | None = None
    cookies_file: Path | None = None
    proxy: str | None = None
    js_runtime: str | None = None
    extractors: tuple[str, ...] = EXTRACTORS
    extra_args: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, env=None) -> "YtdlpConfig":
        env = os.environ if env is None else env
        cookies = env.get("CLIPMAKER_YTDLP_COOKIES") or None
        return cls(
            pot_url=(env.get("CLIPMAKER_YTDLP_POT_URL") or None),
            cookies_file=Path(cookies) if cookies else None,
            proxy=(env.get("CLIPMAKER_YTDLP_PROXY") or None),
            js_runtime=(env.get("CLIPMAKER_YTDLP_JS_RUNTIME") or None),
        )


def ytdlp_args(config: YtdlpConfig, cookies_copy: Path | None = None) -> list[str]:
    """The yt-dlp command-line options that the config turns on (no URL, no format)."""
    args = ["--no-playlist", "--no-cache-dir", "--socket-timeout", "30", "--retries", "3",
            "--use-extractors", ",".join(config.extractors)]
    if config.pot_url:
        args += ["--extractor-args", f"youtubepot-bgutilhttp:base_url={config.pot_url.rstrip('/')}"]
    if config.cookies_file:
        args += ["--cookies", str(cookies_copy or config.cookies_file)]
    if config.proxy:
        args += ["--proxy", config.proxy]
    if config.js_runtime:
        args += ["--js-runtimes", config.js_runtime]
    return args + list(config.extra_args)


def friendly_error(stderr: str) -> str:
    text = (stderr or "").lower()
    for needles, message in _ERRORS:
        if any(n in text for n in needles):
            return message
    return "Couldn't download this link. Upload the video file instead."


def fetch_audio(url: str, dest_dir: Path, config: YtdlpConfig | None = None,
                max_bytes: int = MAX_BYTES, max_seconds: float = MAX_SECONDS) -> Path:
    """Download only the audio of a platform link into `dest_dir`; returns the file path.

    Raises FetchError with a user-safe message (bot check, private video, too long...).
    """
    config = config or YtdlpConfig.from_env()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    cookies_copy = None
    if config.cookies_file:
        if not config.cookies_file.is_file():
            raise FetchError("The configured cookies file is missing (CLIPMAKER_YTDLP_COOKIES).")
        cookies_copy = dest_dir / ".cookies.txt"
        shutil.copyfile(config.cookies_file, cookies_copy)
    cmd = [sys.executable, "-m", "yt_dlp", *ytdlp_args(config, cookies_copy),
           "-f", "bestaudio/best", "--max-filesize", str(max_bytes),
           "--match-filters", f"duration <=? {int(max_seconds)}",
           "-o", str(dest_dir / "audio.%(ext)s"), "--no-progress",
           "--print", "after_move:filepath", "--", url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise FetchError("The download took too long. Upload the file instead.") from exc
    finally:
        if cookies_copy:
            cookies_copy.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise FetchError(friendly_error(proc.stderr))
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines or not Path(lines[-1]).is_file():
        # yt-dlp skips (exit 0, no file) when a match filter or the size limit rejects it
        if "does not pass filter" in proc.stdout + proc.stderr:
            raise FetchError(f"The video is too long (limit {int(max_seconds // 60)} minutes).")
        if "larger than max-filesize" in (proc.stdout + proc.stderr).lower():
            raise FetchError(f"The file is too big (limit {max_bytes // 1024 ** 2} MB).")
        raise FetchError(friendly_error(proc.stderr))
    out = Path(lines[-1])
    # the site may not report a duration up front, so check the file itself too
    from clipmaker.media import probe
    try:
        info = probe(out)
    except Exception as exc:
        out.unlink(missing_ok=True)
        raise FetchError("The downloaded file isn't audio or video ffmpeg can read.") from exc
    if info.duration > max_seconds:
        out.unlink(missing_ok=True)
        raise FetchError(f"The video is too long (limit {int(max_seconds // 60)} minutes).")
    return out


def diagnose(config: YtdlpConfig | None = None) -> dict:
    """What anti-bot help is configured and working (for `clipmaker platform-check`)."""
    import importlib.util

    config = config or YtdlpConfig.from_env()
    report = {"yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
              "pot_plugin": False, "pot_server": None, "cookies": None, "proxy": bool(config.proxy),
              "js_runtime": config.js_runtime or "deno",
              "js_runtime_found": shutil.which(config.js_runtime or "deno") is not None}
    try:
        report["pot_plugin"] = importlib.util.find_spec(POT_PLUGIN_MODULE) is not None
    except ModuleNotFoundError:
        pass
    if report["yt_dlp"]:
        from yt_dlp.version import __version__
        report["yt_dlp_version"] = __version__
    if config.pot_url:
        try:
            with urllib.request.urlopen(config.pot_url.rstrip("/") + "/ping", timeout=5) as resp:
                report["pot_server"] = resp.status == 200
        except OSError:
            report["pot_server"] = False
    if config.cookies_file:
        report["cookies"] = config.cookies_file.is_file()
    return report
