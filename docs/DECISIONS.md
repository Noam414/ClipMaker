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
