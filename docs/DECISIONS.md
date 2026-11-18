# Decisions

The nightly agent records every significant technical decision here, newest first.

Template:

```
## D-NNN: <short title> (YYYY-MM-DD)
- Decision:
- Alternatives considered:
- Why:
- How to reverse:
```

## D-004: Optional LLM re-ranking via Anthropic Messages API, off by default (2026-11-18)
- Decision: `clipmaker/llm_scoring.py` defines a `Scorer` interface and an `AnthropicScorer` (plain `urllib`, no SDK dependency). Enabled only by `CLIPMAKER_LLM_SCORING=1` plus `ANTHROPIC_API_KEY`; scores the top 12 heuristic candidates in one request and blends 50/50 with the heuristic score. Any error falls back to heuristics. Default model `claude-haiku-4-5` (cheap, fast), overridable with `CLIPMAKER_LLM_MODEL`.
- Alternatives considered: a local LLM (too slow on 2 CPUs, weights on blocked hosts); OpenAI (either works; the interface allows adding it).
- Why: keeps the default fully local with nothing phoning home, while leaving room for better ranking when the owner wants it (H-004).
- How to reverse: leave the flag unset (default), or delete the module and its two-line call in `pipeline.py`.

## D-003: Highlight scoring by heuristics + clip selection by weighted interval scheduling (2026-11-18)
- Decision: `clipmaker/highlights.py` scores each candidate from five 0..1 signals (opening hook 0.4, content density 0.2, length near 30 s 0.2, completeness 0.1, pacing 0.1), minus penalties for opening or closing on filler/outro. English + Hebrew word lists. `select_clips` then picks up to N non-overlapping clips maximizing the sum of `score - 0.6`, so one long clip can't block two strong ones and hook-less clips are dropped (but at least one clip is always returned). Clip edges must sit in a pause of at least 0.3 s.
- Alternatives considered: greedy best-first selection (tested: one long middle clip blocked both hooks, hook-start rate 0.5); an ML/LLM ranker (planned as an optional, off-by-default scorer in M2; needs an API key).
- Why: transparent, fast, no network, and each clip's `score_parts` in `clips.json` shows why it was picked.
- How to reverse: change `WEIGHTS`/word lists in `highlights.py`, or pass `min_score=0` to `select_clips` to keep weaker clips.

## D-002: Transcription: Silero VAD + Whisper base (sherpa-onnx, int8), proportional word timing (2026-11-18)
- Decision: speech chunks from Silero VAD (min silence 0.3 s, max chunk 20 s), each chunk transcribed by Whisper **base** int8 through `sherpa-onnx` on CPU. Word timestamps are spread inside each VAD chunk in proportion to word length. Models are downloaded at runtime from the `k2-fsa/sherpa-onnx` GitHub release `asr-models` into `$CLIPMAKER_CACHE_DIR/models` (only the int8 files are kept). `CLIPMAKER_WHISPER_MODEL=tiny|base|small` overrides the size.
- Licenses: sherpa-onnx Apache-2.0; Whisper weights MIT (OpenAI); Silero VAD MIT.
- Alternatives considered: faster-whisper / whisper.cpp (weights on huggingface.co, blocked here); openai-whisper (PyTorch, heavy, weights host blocked); Vosk (weaker accuracy, no good Hebrew); Whisper small (tested on the Hebrew fixture: no better, 3x the download).
- Why: runs on 2 CPUs at ~12x real time with base, ~7% WER on the English podcast fixture, no blocked hosts. sherpa-onnx's Whisper API doesn't return token timestamps, so VAD chunk edges give the anchor points; sentence ends usually coincide with a pause, which is what clip boundaries need.
- Known limit: the Hebrew fixture is spoken by espeak-ng, whose Hebrew voice is barely intelligible, so its WER (~0.97) says more about the fixture than the model. See H-003.
- How to reverse: `clipmaker/transcribe.py` is the only module that touches the ASR; replace `transcribe()` and keep the `Transcript` shape. If huggingface.co opens (H-001), faster-whisper with real word timestamps is the natural upgrade.

## D-001: Stack: Python + FastAPI + SQLAlchemy/SQLite + DB-backed job queue + server-rendered UI (2026-11-18)
- Decision:
  - **Language:** Python 3.11+ for everything (pipeline, API, worker). One package, `clipmaker`, under `src/`.
  - **Backend:** FastAPI served by uvicorn.
  - **Database:** SQLAlchemy 2.x, configured by one `DATABASE_URL` setting. Default `sqlite:///./data/clipmaker.db`; PostgreSQL works by changing the URL (e.g. `postgresql+psycopg://...`). Migrations with Alembic once M5 adds real models.
  - **Job queue:** a small DB-backed queue (a `jobs` table polled by a worker process/thread) instead of Celery/RQ. No Redis needed.
  - **Frontend:** server-rendered Jinja2 templates with plain JS (HTMX if it helps), static files served by FastAPI. No Node build step.
  - **Media:** ffmpeg/ffprobe via subprocess.
  - **Tests:** pytest. **CI:** GitHub Actions (ubuntu-latest, ffmpeg + espeak-ng from apt).
- Alternatives considered: Node/TypeScript backend (the ML/media ecosystem is Python-first); Django (heavier than needed); Celery or RQ + Redis (Redis is awkward on Windows and adds a service for a single-user app); React/Vite SPA (adds a second toolchain and build step; can be added later behind the same API).
- Why: one language, runs natively on Windows with `pip install -e .` and `clipmaker serve`, or with `docker compose up`. SQLite needs no account and no service; `DATABASE_URL` keeps the move to hosted Postgres a config change.
- How to reverse: the queue sits behind `clipmaker.jobs` and the DB behind `clipmaker.db`, so swapping in RQ/Celery or Postgres touches only those modules. A SPA frontend can replace `src/clipmaker/web/templates` while keeping the API.

## D-000: Autonomous nightly development on `claude/nightly` (2026-11-17)
- Decision: A scheduled Claude Code task runs every night, develops on branch `claude/nightly`, and keeps one PR open to `main`. The owner merges when they choose. All technical choices are delegated to the agent.
- Alternatives considered: pushing straight to `main`; opening a new PR every night.
- Why: Work builds up from night to night even if the PR isn't merged right away, and `main` stays under the owner's control.
- How to reverse: Edit the scheduled task's prompt and the "Nightly workflow" section of `CLAUDE.md`.
