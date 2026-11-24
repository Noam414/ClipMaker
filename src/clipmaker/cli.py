"""Command line entry point.

    clipmaker serve [--host H] [--port P]   run the web app
    clipmaker probe <file>                  print media info as JSON
    clipmaker platform-check                show the YouTube/Twitch (yt-dlp) anti-bot setup
    clipmaker <input.mp4> --out <dir>       make clips (pipeline; grows through M1)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from clipmaker import __version__


def _serve(argv: list[str]) -> int:
    from clipmaker.config import get_settings

    settings = get_settings()
    parser = argparse.ArgumentParser(prog="clipmaker serve")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args(argv)

    import uvicorn

    from clipmaker.web.app import create_app

    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


def _platform_check(argv: list[str]) -> int:
    from clipmaker.platforms import diagnose

    report = diagnose()
    print(json.dumps(report, indent=2))
    problems = []
    if not report["yt_dlp"]:
        problems.append("yt-dlp isn't installed: pip install yt-dlp")
    if report["pot_server"] is False:
        problems.append("CLIPMAKER_YTDLP_POT_URL is set but the PO-token server doesn't answer /ping")
    if report["pot_server"] and not report["pot_plugin"]:
        problems.append("PO-token server is up but the plugin is missing: pip install bgutil-ytdlp-pot-provider")
    if report["cookies"] is False:
        problems.append("CLIPMAKER_YTDLP_COOKIES points to a missing file")
    if not report["js_runtime_found"]:
        problems.append(f"JavaScript runtime {report['js_runtime']!r} not found; YouTube needs one "
                        "(set CLIPMAKER_YTDLP_JS_RUNTIME=node if Node is installed)")
    for line in problems:
        print("problem:", line, file=sys.stderr)
    return 1 if problems else 0


def _probe(argv: list[str]) -> int:
    from clipmaker.media import probe

    parser = argparse.ArgumentParser(prog="clipmaker probe")
    parser.add_argument("file")
    args = parser.parse_args(argv)
    print(json.dumps(asdict(probe(args.file)), indent=2))
    return 0


def _process(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="clipmaker", description="Turn a long video into short vertical clips.")
    parser.add_argument("input", help="input video file, a direct http(s) link to one, "
                                      "or a YouTube/Twitch video link (with --rights)")
    parser.add_argument("--rights", action="store_true",
                        help="confirm you own the linked YouTube/Twitch video or have the rights to use it")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--language", default=None, help="speech language code, e.g. en or he (default: auto-detect)")
    parser.add_argument("--no-transcribe", action="store_true", help="skip speech-to-text")
    parser.add_argument("--no-captions", action="store_true", help="don't burn in captions")
    parser.add_argument("--max-clips", type=int, default=5)
    parser.add_argument("--keep-pauses", action="store_true", help="don't shorten long pauses")
    parser.add_argument("--caption-style", choices=["basic", "bold", "box", "pop"], default="bold",
                        help="caption look; all but 'basic' highlight the spoken word")
    parser.add_argument("--no-keywords", action="store_true", help="don't colour the key word of each caption line")
    parser.add_argument("--emoji", action="store_true", help="pop a matching symbol above key words")
    parser.add_argument("--no-title", action="store_true", help="no hook title card in the first 3 s")
    parser.add_argument("--no-progress-bar", action="store_true", help="no progress bar at the bottom")
    parser.add_argument("--no-zoom", action="store_true", help="no zoom punch-ins on strong key words")
    parser.add_argument("--static-crop", action="store_true", help="centre crop instead of following the speaker")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    from clipmaker.pipeline import run_pipeline

    source = args.input
    from clipmaker.fetch import FetchError, download, filename_from_url, is_url

    from clipmaker.platforms import platform_link_ok, platform_of

    video_source = None
    if is_url(source) and platform_of(source):
        if not args.rights:
            print("clipmaker: add --rights to confirm you own this video or have the rights to use it",
                  file=sys.stderr)
            return 2
        if not platform_link_ok(source):
            print("clipmaker: paste the link of a single video, VOD or clip", file=sys.stderr)
            return 2
        from clipmaker.media import probe
        from clipmaker.platforms import fetch_audio, section_source

        try:  # audio first; only the selected clips' video is downloaded later (M5.5)
            url = source
            source = fetch_audio(url, Path(args.out) / "source")
            video_source = section_source(url, Path(args.out) / "source" / "sections", probe(source).duration)
        except FetchError as exc:
            print(f"clipmaker: {exc}", file=sys.stderr)
            return 2
    elif is_url(source):
        try:
            source = download(source, Path(args.out) / "source" / filename_from_url(source))
        except FetchError as exc:
            print(f"clipmaker: {exc}", file=sys.stderr)
            return 2
    result = run_pipeline(source, args.out, language=args.language, transcribe=not args.no_transcribe,
                          captions=not args.no_captions, max_clips=args.max_clips,
                          remove_dead_air=not args.keep_pauses, reframe=not args.static_crop,
                          caption_style=args.caption_style, keywords=not args.no_keywords,
                          emoji=args.emoji, hook_title=not args.no_title,
                          progress_bar=not args.no_progress_bar, zoom=not args.no_zoom,
                          video_source=video_source)
    print(json.dumps({"clips": len(result["clips"]), "out": str(args.out)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        return _serve(argv[1:])
    if argv and argv[0] == "platform-check":
        return _platform_check(argv[1:])
    if argv and argv[0] == "probe":
        return _probe(argv[1:])
    if argv and argv[0] in ("--version", "-V"):
        print(__version__)
        return 0
    return _process(argv)


if __name__ == "__main__":
    raise SystemExit(main())
