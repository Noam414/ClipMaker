# ClipMaker: instructions for the autonomous nightly agent

This repository is developed mostly by Claude Code running unattended every
night. The owner (Noam) reviews the work through a pull request. Read this
whole file at the start of every run.

## What we are building

ClipMaker turns long videos (podcasts, talks, streams) into short vertical
clips ready for TikTok, Reels and Shorts. It aims to match what OpusClip offers,
under its own name and design.

Core pipeline:

1. **Ingest**: upload a file (and later, import from a direct media URL)
2. **Transcribe**: speech-to-text with word-level timestamps, English and Hebrew
3. **Find highlights**: candidate moments, scored for hook strength, completeness and pacing
4. **Cut**: clips start and end on sentence boundaries, with dead air and filler removed
5. **Reframe**: 16:9 to 9:16, following the speaker with a smooth virtual camera
6. **Captions**: animated word-by-word captions in several styles, with correct right-to-left Hebrew
7. **Effects**: zoom punch-ins, keyword highlight, emoji, progress bar, hook title, later B-roll
8. **Export**: MP4 at 1080x1920, with 1:1 and 16:9 as options
9. **Web app**: upload, job progress, a clip gallery with scores, preview, caption editing, download

`docs/ROADMAP.md` holds the milestones and is the source of truth for what comes next.

## You make the decisions

The owner has delegated **all technical decisions** to you: language,
frameworks, database, models, architecture, UI design. Do not wait for
approval. Follow these rules:

- **Record decisions.** Write every significant decision in `docs/DECISIONS.md`: what you chose, the alternatives, why, and how to reverse it.
- **Stay consistent.** Reverse an earlier decision only with a written reason in `DECISIONS.md`. Never switch stacks on a whim.
- **Prefer boring tools.** Choose well-maintained, free, open-source tools that run inside the VM described below.
- **Route around blockers.** Anything that needs a human goes in `docs/NEEDS_HUMAN.md` with exact steps: paid accounts, sign-ups, API keys, network allowlist changes, deployment, legal or product calls. Then build around it with a fallback, mock or local alternative. Don't stop the night's work.
- **Pick the database.** The owner has not chosen one. Pick one that runs locally in the VM with no external account, such as SQLite or the preinstalled PostgreSQL. Put access behind a single `DATABASE_URL`-style setting so it can move to a hosted database later.

## The environment you run in

- **The VM**: a fresh Ubuntu 24.04 VM every run, x86_64, about 2 CPUs and 8 GB RAM, **no GPU**.
- **Preinstalled**: Python 3.13, Node 22, Docker, PostgreSQL 16 and Redis 7. Postgres and Redis are not running; start them with `service postgresql start` and `service redis-server start`.
- **Media tools**: `ffmpeg` should be present. If it's missing, run `apt-get update && apt-get install -y ffmpeg`. `espeak-ng`, used by the fixture generator, usually needs `apt-get install -y espeak-ng`.
- **Network**: the network is allowlisted.
  - **Works**: PyPI, npm, Ubuntu apt, GitHub, including GitHub release assets. For example, `k2-fsa/sherpa-onnx` publishes Whisper ONNX models as release assets.
  - **Blocked**: most other hosts, including **huggingface.co**. Blocked requests fail or return 403 `host_not_allowed`.
  - **When blocked**: if a dependency needs a blocked host, use an alternative or add the host to `NEEDS_HUMAN.md`. Each night, re-test the blocked hosts listed there, since the owner may have opened them.
- **Persistence**: nothing survives between nights except what is pushed to git. Don't commit files over about 5 MB, model weights or generated videos. Download models at runtime into a cache directory that git ignores.
- **Time**: keep a full eval run under about 10 minutes on CPU. Prefer small models (tiny or base) for tests.

## Usage budget

Every run counts against the owner's Claude plan, which his other projects also
use. If a run uses up the plan, the owner can't work during the day. Keep runs lean:

- **Single context.** Don't spawn subagents or agent teams, and don't start other sessions.
- **Small context.** Don't read large files, logs or generated output in full. Use `head`, `grep`, `jq` and short summaries. Look at no more than about 6 frames per night.
- **Time box.** Plan for about 60–75 minutes of work. After that, don't start new items.
- **Save as you go.** After each finished item with green tests, commit, push, and update the log entry and the PR description right away. If a usage limit cuts the run short, nothing is lost and the next night continues from git.
- **Split big items.** If an item is too big for one night, split it in `ROADMAP.md` and do only the first part.

## Nightly workflow

1. **Sync.** Work on branch `claude/nightly`. Merge `origin/main` into it first. If the owner changed a file on `main`, the owner's version wins.
2. **Orient.** Read the last 3 entries of `docs/NIGHTLY_LOG.md`, plus `docs/ROADMAP.md`, `docs/DECISIONS.md` and `docs/NEEDS_HUMAN.md`. Check whether any NEEDS_HUMAN item was resolved.
3. **Fixtures.** Run `python3 scripts/make_test_videos.py`.
4. **Baseline.** Run the test suite and the eval (once they exist). Save the numbers.
5. **Choose work.** Pick 1–3 focused items in this order:
   1. Anything broken: failing tests, a crashing pipeline, red CI
   2. The next unchecked item in `ROADMAP.md`
   3. The weakest eval metric, or the worst problem visible in the output frames
6. **Implement.** Write tests with the code. After each change, re-run the tests and the eval. Keep a change only if the tests stay green and no metric regresses, or explain the trade-off in the log.
7. **Look at the output.** Extract frames from the output clips with ffmpeg and open the PNGs to inspect them. Check that:
   - captions are readable, inside the frame and in the right order (Hebrew RTL)
   - the face stays inside the crop
   - there are no black bars and nothing is stretched
8. **Commit.** Make small, logical commits with English messages. Push `claude/nightly` after **each** finished item, not only at the end (see "Usage budget").
9. **Pull request.** Make sure a PR from `claude/nightly` to `main` is open. Update its description in **Hebrew** with the overall state of the project, tonight's changes, metrics before and after, what the owner should look at, and open NEEDS_HUMAN items. In cloud sessions GitHub GraphQL is unavailable, so `gh pr create` and `gh pr edit` fail; use the REST API (`gh api repos/Noam414/ClipMaker/pulls ...`) instead. Push with `git -c push.negotiate=true push origin claude/nightly`.
10. **Log.** Add an entry in **Hebrew** to the top of `docs/NIGHTLY_LOG.md`, using the template in that file. Create it after the first finished item and update it after each later one.
11. **Update.** Tick the finished items in `ROADMAP.md` and update `README.md` if run instructions changed.

## Eval contract

The first milestone must create an eval command, for example `python3 scripts/eval.py`, that:

- **Runs** the full pipeline on every fixture in `tests/fixtures/videos/manifest.json`.
- **Writes results** to `eval/results/latest.json` and appends one line per run to `eval/results/history.jsonl`. Commit both.
- **Measures**, wherever the pipeline supports it:
  - transcription word error rate against the `speech_segments` text
  - the share of clips that start and end within 0.5 s of a sentence boundary
  - the share of clips that start on a `hook` segment
  - seconds of `dead_air` left in the output
  - reframing: the share of sampled frames where the `face_track` box sits fully inside the crop
  - scene-cut precision and recall against `scene_cuts`, within ±0.2 s
  - output validity: ffprobe passes, 1080x1920, audio present, duration within limits
  - processing seconds per minute of input
- **Saves contact sheets**: one PNG per output clip in `eval/results/frames/` (git-ignored), for visual review.

Ground-truth speech timings are accurate to about ±0.5 s, because espeak adds a short silent tail.
`face_track` is accurate to about 3 px. Add new fixtures by extending
`scripts/make_test_videos.py`. Never download copyrighted videos for tests.

## Hard rules

- **Git**: never push to `main`, force-push, or rewrite history on `claude/nightly`.
- **Repository**: never delete branches you did not create. Don't change repository settings, secrets, or GitHub Actions permissions.
- **Data and services**: never touch real user data or production services. Use a local database inside the VM. Connect to an external database only after `NEEDS_HUMAN.md` records that the owner provided a staging one, and then only to that one.
- **Secrets**: no secrets in git. Read them from environment variables only. Ship a `.env.example` file.
- **Dependencies**: only permissive or compatible open-source licenses. Record the license of every model in `DECISIONS.md`. No telemetry and no code that phones home.
- **Branding**: don't use OpusClip's name, logo, UI copy or assets. The product is **ClipMaker**, with its own visual identity.
- **URL import**: support direct media URLs. Anything platform-specific, such as YouTube, is a product and legal decision for the owner, so put it in `NEEDS_HUMAN.md`.
- **Irreversible steps**: if something is both ambiguous and irreversible, write it in `NEEDS_HUMAN.md` and skip it.

## Conventions

- **Language**: code, comments and commit messages in English. `NIGHTLY_LOG.md` and PR descriptions in Hebrew.
- **Running locally**: the owner works on **Windows**. Keep local runs easy, with `docker compose up` or a short Python or Node command, and document it in `README.md`.
- **CI**: add CI (GitHub Actions) that runs the tests on every PR, as part of milestone M0.
