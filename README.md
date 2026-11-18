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
Requires `ffmpeg` and `espeak-ng`.

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
clipmaker input.mp4 --out clips  # command-line pipeline (grows through milestone M1)
```

Settings live in environment variables or `.env` (see `.env.example`). The database
defaults to a local SQLite file; set `DATABASE_URL` to use PostgreSQL.

## Tests and eval

```
pip install -e ".[dev]"
python -m pytest -q
python scripts/eval.py            # needs the test videos above
```

The eval writes `eval/results/latest.json` and appends to `eval/results/history.jsonl`.
