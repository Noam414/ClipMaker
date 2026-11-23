# ClipMaker

Turn long videos into short vertical clips with smart highlights, speaker-tracking
reframing and animated captions (English and Hebrew).

This project is developed by an autonomous Claude Code agent that runs every night
on branch `claude/nightly`. Its progress is reviewed through a pull request.

- What it's building and how it works: [`CLAUDE.md`](CLAUDE.md)
- Plan: [`docs/ROADMAP.md`](docs/ROADMAP.md)
- Technical decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md)
- Nightly log (Hebrew): [`docs/NIGHTLY_LOG.md`](docs/NIGHTLY_LOG.md)
- Things that need the owner: [`docs/NEEDS_HUMAN.md`](docs/NEEDS_HUMAN.md)

## Test videos

```
python3 scripts/make_test_videos.py
```

Generates synthetic fixtures with exact ground truth in `tests/fixtures/videos/`.
Requires `ffmpeg` and `espeak-ng`. This includes `long_talk`, an ~11-minute video for the
speed eval (about 2 extra minutes to generate); add `--no-long` to skip it.

`python3 scripts/eval.py` scores every fixture and reports `time_to_all_clips_s` and
`extrapolated_60min_s` (the goal is all clips of a 60-minute video within 300 s).

## Running locally

You need [ffmpeg](https://ffmpeg.org/download.html) on your PATH (Windows: `winget install Gyan.FFmpeg`).

### Option A: Docker (any OS)

```
docker compose up --build
```

Then open http://localhost:8000.

### Option B: Python 3.11+ (Windows PowerShell shown; same on macOS/Linux)

```
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env           # optional; macOS/Linux: cp
clipmaker serve                  # web app on http://127.0.0.1:8000
clipmaker input.mp4 --out clips  # make clips from a video
```

`clipmaker input.mp4 --out clips` transcribes the video, picks up to 5 clips of
15–60 s on sentence boundaries ranked by hook strength, shortens long pauses, reframes to 9:16 following the speaker's face, and writes `clip_01.mp4`… (1080x1920, captions burned in)
plus `clips.json` with timings, text and scores. Options: `--language en|he`
(default: auto-detect), `--max-clips N`, `--no-captions`, `--keep-pauses` (don't shorten pauses over 1 s), `--static-crop` (centre crop, no face following), `--caption-style bold|box|pop|basic` (all but `basic` highlight the spoken word), `--no-keywords` (don't colour the key word of each caption line), `--emoji` (pop a matching symbol above key words), `--no-title` (no hook title card in the first 3 s), `--no-progress-bar`, `--no-zoom` (no zoom punch-ins on strong key words). The input can also be a direct
link to a video file (`clipmaker https://example.com/talk.mp4 --out clips`; up to 60 minutes and 4 GB, public
addresses only). The first run downloads the speech models (Whisper base ~200 MB for Hebrew and language detection,
Moonshine base ~240 MB for English) and a small face detector (~230 KB) into `.cache/models`.
`CLIPMAKER_EN_ENGINE=whisper` uses Whisper for English too; `CLIPMAKER_WHISPER_MODEL=tiny` picks a smaller,
less accurate Whisper.

`clipmaker platform-check` shows the optional YouTube/Twitch (yt-dlp) settings — PO-token provider,
cookies, proxy — from `.env.example`; platform links aren't wired into the pipeline yet.

**Web app:** `clipmaker serve`, then open http://127.0.0.1:8000, drop a video on the page (or paste a
direct link to a video file) and watch the progress bar; the clips are listed (with links to the MP4s) when the job is done.
Uploads and clips are stored under `CLIPMAKER_DATA_DIR/projects/<id>/`. The web process runs
one background worker; set `CLIPMAKER_WORKER=0` to turn it off. The database schema is created
and upgraded automatically on start (Alembic migrations in `src/clipmaker/migrations`).

Settings live in environment variables or `.env` (see `.env.example`). The database
defaults to a local SQLite file; set `DATABASE_URL` to use PostgreSQL.

## Tests and eval

```
pip install -e ".[dev]"
python -m pytest -q
python scripts/eval.py            # needs the test videos above
```

The browser end-to-end test (`tests/test_e2e_web.py`) is skipped unless Playwright is set up:
`pip install -e ".[dev,e2e]"` and `python -m playwright install chromium`.

The eval writes `eval/results/latest.json` and appends to `eval/results/history.jsonl`.
