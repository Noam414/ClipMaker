"""Command line entry point.

    clipmaker serve [--host H] [--port P]   run the web app
    clipmaker probe <file>                  print media info as JSON
    clipmaker <input.mp4> --out <dir>       make clips (pipeline; grows through M1)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

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


def _probe(argv: list[str]) -> int:
    from clipmaker.media import probe

    parser = argparse.ArgumentParser(prog="clipmaker probe")
    parser.add_argument("file")
    args = parser.parse_args(argv)
    print(json.dumps(asdict(probe(args.file)), indent=2))
    return 0


def _process(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="clipmaker", description="Turn a long video into short vertical clips.")
    parser.add_argument("input", help="input video file")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--language", default=None, help="speech language code, e.g. en or he (default: auto-detect)")
    parser.add_argument("--no-transcribe", action="store_true", help="skip speech-to-text")
    parser.add_argument("--no-captions", action="store_true", help="don't burn in captions")
    parser.add_argument("--max-clips", type=int, default=5)
    parser.add_argument("--keep-pauses", action="store_true", help="don't shorten long pauses")
    parser.add_argument("--caption-style", choices=["basic", "bold", "box", "pop"], default="bold",
                        help="caption look; all but 'basic' highlight the spoken word")
    parser.add_argument("--static-crop", action="store_true", help="centre crop instead of following the speaker")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    from clipmaker.pipeline import run_pipeline

    result = run_pipeline(args.input, args.out, language=args.language, transcribe=not args.no_transcribe,
                          captions=not args.no_captions, max_clips=args.max_clips,
                          remove_dead_air=not args.keep_pauses, reframe=not args.static_crop,
                          caption_style=args.caption_style)
    print(json.dumps({"clips": len(result["clips"]), "out": str(args.out)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        return _serve(argv[1:])
    if argv and argv[0] == "probe":
        return _probe(argv[1:])
    if argv and argv[0] in ("--version", "-V"):
        print(__version__)
        return 0
    return _process(argv)


if __name__ == "__main__":
    raise SystemExit(main())
