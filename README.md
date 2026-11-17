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

Not runnable yet. The agent will document local run instructions (Windows included) here.
