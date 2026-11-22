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

## D-016: English speech on Moonshine base; language identified with Whisper when not given (2026-11-22)
- Decision: English is transcribed with Moonshine base (sherpa-onnx int8, from the k2-fsa GitHub release; **licence: MIT**, Useful Sensors, English models). Hebrew and every other language stay on Whisper base (D-002). When no language is given (web uploads), the language is identified from the first 30 s of speech with the Whisper base encoder (sherpa-onnx `SpokenLanguageIdentification`), then routed. `CLIPMAKER_EN_ENGINE=whisper` restores the old behaviour; `transcribe(model=...)` still forces Whisper.
- Benchmark (2 min of `long_talk`, the same VAD chunks, 2 threads, x86 VM): Whisper base 13.6 s / WER 0.104; Whisper tiny 7.1 s / 0.228; Moonshine base 5.7 s / 0.121; Moonshine tiny 3.9 s / 0.156. Merging VAD chunks into ~25 s windows didn't speed Whisper up here (and sherpa-onnx returns no Whisper token timestamps), so it wasn't adopted. Full eval: transcription of `long_talk` 58.8 → 34.3 s; mean English WER about the same (0.111 → 0.108: portrait 0.12 → 0.21, two speakers 0.15 → 0.03).
- Known limits: language ID costs about 0.5 s per video; espeak's Hebrew is identified as English (it's unintelligible anyway, and the eval passes `he` explicitly), real Hebrew should be identified correctly, but if it isn't, the English model would produce nonsense — the upload form could get a language picker later. Moonshine adds a ~240 MB one-time download.
- Alternatives considered: Whisper tiny (2x faster but WER doubles); Moonshine tiny (fastest, but worse WER than base); Parakeet/zipformer transducers (would give real word timestamps for D-006, but larger downloads and not benchmarked yet).
- How to reverse: set `CLIPMAKER_EN_ENGINE=whisper`, or change the default in `transcribe.english_engine()`.

## D-015: Speed eval method; scene detection only on the selected clip ranges (2026-11-22)
- Decision: the speed eval runs the full pipeline on `long_talk`, a ~11-min synthetic fixture (the English scripts looped, 1920x1080), and reports `time_to_all_clips_s` (wall clock, model loading included) with per-stage timings. The 60-min value is extrapolated: stages that scale with input length are multiplied by 60/input-minutes; per-clip stages (render, and scene detection once it only scans the clips) are kept as they are, because `max_clips` caps the work. The speed fixture is kept out of the summary's quality averages so they stay comparable with earlier runs. CI skips this fixture (`make_test_videos.py --no-long`) to keep CI short.
- Pipeline order (D-013): scene detection now runs after clip selection, on each clip's range ±2 s (merged when they overlap), with input seeking, instead of on the whole video. A video without speech (no clips) is still scanned in full so scene cuts are reported. `clips.json` says which in `scene_cuts_scope` ("clips" or "full"). Audio was already decoded with `-vn`, and face tracking and rendering were already per clip.
- Known limits: measured on the agent's 2-core x86 VM, not on the 2-core ARM Oracle server (M7 ARM check); the content repeats, so clip choice on `long_talk` isn't meaningful. The scene-cut list in `clips.json` no longer covers the whole video.
- Alternatives considered: a real 60-min fixture (too slow to generate and run nightly, > 10 min eval budget); linear extrapolation of the total (overstates render, which doesn't grow with length).
- How to reverse: `pipeline.py` — call `detect_scene_cuts(input_path)` before transcription again.

## D-014: Accept YouTube and Twitch links via yt-dlp; social publishing and paid automation later (2026-11-21, owner decision)
- Decision: the owner decided (H-005) that the site accepts YouTube and Twitch links in addition to direct media links. They're fetched with yt-dlp (Unlicense), which fits the audio-first plan (D-013): it can fetch an audio-only format and then download only chosen time sections. Before processing, the user must confirm they own or have the rights to the video. Later milestones: publishing clips to connected YouTube/TikTok accounts (M8) and paid plans with automation (M9).
- Risks the owner accepted: the platforms' terms of service restrict downloading (product/legal risk, owner's call; the agent is not a lawyer); platforms often block datacenter IPs (e.g. YouTube's "confirm you're not a bot"), which may hit the Oracle server, so a link can fail and the UI must offer "upload the file instead"; yt-dlp needs frequent updates as sites change, so the deploy kit must update it regularly. youtube.com and twitch.tv are blocked in the agent's VM, so nightly tests use a local fake site; real-world checks need the owner or the live server.
- Alternatives considered: direct links only (safest, but the owner wants platform links); official APIs (neither YouTube nor Twitch offers a video-download API for other people's videos).
- How to reverse: platform support sits behind one fetcher module; removing yt-dlp leaves direct links working.

## D-013: Speed target ≤ 5 min, audio-first link pipeline, Oracle free tier as the host (2026-11-21, owner request)
- Decision: the owner wants every clip ready within 5 minutes of pasting a link, and a public website that is free to run. Target: a 60-minute video in ≤ 300 s on 2 ARM cores. The pipeline is reordered: audio first → transcript → clip selection from text alone → only the selected video ranges are fetched (or decoded, for uploads) for scene detection, reframing and rendering. Clips appear in the gallery one by one. Hosting target: Oracle Cloud Always Free Ampere A1 (cut in 2026 from 4 cores/24 GB to 2 cores/12 GB), with Docker, Caddy (HTTPS) and SQLite on the instance; guardrails (length limits, per-visitor quota, 24 h retention) keep one free server usable.
- Alternatives considered: Hugging Face Spaces (free CPU tier was restricted in 2026, and huggingface.co is blocked for the agent); processing in the visitor's browser (WASM/WebGPU: free to host, but speed then depends on the visitor's hardware, which the owner explicitly doesn't want, and it's a rewrite); the owner's own PC behind a Cloudflare Tunnel (free, but only up while the PC is on — kept as a fallback); paid GPU hosting (not free).
- Known limits: a single MP4 file interleaves audio and video, so "audio only" from such a link still reads most of the file's bytes (fast server-side, but not free of bandwidth); true audio-only savings come from HLS/DASH sources with separate audio tracks. One free server handles one video at a time; at real traffic it needs paid capacity. Oracle can change the free tier again.
- How to reverse: the pipeline order lives in `pipeline.py`; hosting is a deploy kit under `deploy/oracle/` on a standard Docker image, so moving to any VPS is a config change.

## D-012: Web jobs: DB-backed queue in the web process, Alembic migrations, progress by polling (2026-11-21)
- Decision: tables `projects`, `jobs`, `clips` (SQLAlchemy ORM in `clipmaker/store.py`), schema owned by Alembic migrations in `src/clipmaker/migrations` and applied automatically on app start (`init_db` = `alembic upgrade head`; `render_as_batch` so SQLite can alter tables). `POST /api/projects` streams the upload to `data/projects/<id>/source.<ext>` and enqueues a job; one worker thread inside the web process (`clipmaker.jobs.Worker`) claims jobs with a guarded `UPDATE … WHERE status='queued'` and runs `run_pipeline` with a new `progress(stage, fraction)` callback that writes stage and progress to the job row. Jobs left `running` by a crashed process are re-queued at start. The page polls `GET /api/jobs/<id>` every second. Clip MP4s are served from `/media/<project>/clips/<name>` (with HTTP range requests, so the player can seek). The gallery `/projects/<id>` is a server-rendered page (cards with a 9:16 `<video>`, score 0–100, score parts, title, collapsible transcript, `dir=auto` for Hebrew); while the job runs it reloads itself every 3 s.
- Alternatives considered: Celery/RQ + Redis (an extra service, awkward on Windows); a separate worker process (better isolation, but two commands to start on Windows — easy to add later, the queue already supports several workers); WebSockets/SSE for progress (polling once a second is plenty for minute-long jobs and has no proxy pitfalls); `create_all` without migrations (no upgrade path for existing databases).
- E2E: `tests/test_e2e_web.py` drives the real flow (upload → progress → gallery → video metadata loads) with Playwright + headless Chromium (Apache-2.0) against uvicorn in a thread; it skips when Playwright or its browser is missing, and CI installs both (`.[e2e]` extra).
- Known limits: one job at a time per web process; the pipeline isn't cancellable mid-job; no auth yet (M6).
- How to reverse: set `CLIPMAKER_WORKER=0` and run workers elsewhere; the queue functions in `jobs.py` are the only place that touches job state.

## D-011: Zoom punch-ins as a step zoom with ffmpeg zoompan (2026-11-20)
- Decision: when a caption line's keyword is a number or a strong word (not just a hook word), the frame jumps to 112 % from the moment the word is spoken until the line ends (0.4–2 s), at most one punch-in every 4 s (`keywords.punch_ins`). Rendered by `zoompan` with `d=1` at the source frame rate, after the 1080x1920 scale and before the subtitles, so captions, title and bar are not zoomed. The zoom is about the horizontal centre and 35 % of the height, where faces usually are. On by default, `--no-zoom` turns it off; `clips.json` lists `punch_ins` on the output timeline.
- Alternatives considered: smooth eased zoom (zoompan pans in whole pixels, so slow zooms wobble; a hard jump-cut zoom is also the common short-form look); `scale` with `eval=frame` + `crop` (changing frame size mid-graph is fragile); zooming on the face box from the reframe plan (more precise, but the plan is in source time and the zoom in output time — a later refinement).
- Known limits: at 112 % about 5 % of the frame width is lost on each side, The face-inside-crop eval metric accounts for it since 2026-11-21: during a punch-in it checks the face against the zoomed visible window (`metrics._visible_crop`), mapping source time to output time through `kept_ranges`; samples in cut-out dead air are skipped. `clips.json` records `punch_in_scale`. Variable-frame-rate sources could drift slightly because zoompan outputs a fixed rate.
- How to reverse: `run_pipeline(..., zoom=False)`, or change `scale`/`min_gap` in `keywords.py` and `ZOOM_ANCHOR_Y` in `render.py`.

## D-010: Hook title card and progress bar drawn in the same ASS file (2026-11-20)
- Decision: both overlays are ASS events next to the captions, so the renderer is unchanged. The title (`keywords.hook_title`) is the clip's first sentence without leading filler, at most 7 words (ellipsis if cut), shown for the first 3 s at the top (y=250) on an opaque card in the style's accent colour, with a short fade. The progress bar is a 14 px vector rectangle (`\p1`) on the bottom edge whose `\fscx` grows from 0 to 100 % over the clip's output duration. Both on by default; `--no-title`, `--no-progress-bar`. `clips.json` gets `title` and `progress_bar`.
- Alternatives considered: ffmpeg `drawbox`/`drawtext` filters (one more filter chain to keep in sync with the crop/concat graph, and drawtext needs its own font/RTL handling); an LLM-written headline (better copy, but needs H-004; can replace `hook_title` later).
- Why: libass already does Hebrew shaping and RTL, the timing is exact, and the whole look of a clip stays in one editable file — useful for the caption editor in M5.
- How to reverse: `run_pipeline(..., hook_title=False, progress_bar=False)`, or drop `_overlay_events` in `captions.py`.

## D-009: Keyword emphasis and symbol "emoji" in captions (2026-11-20)
- Decision: each caption line gets at most one keyword (`clipmaker/keywords.py`): numbers first, then strong/emotional words, then hook words (word lists shared with `highlights.py`), ties to the longer word. It stays in the style's `keyword_colour` (green in `bold`, yellow in the others) for the whole line; while spoken it takes the normal active-word look. On by default (`--no-keywords` turns it off). With `--emoji`, a keyword that maps to a concept (love, mistake, warning, star, check, energy, smile) pops a coloured symbol (❤ ✗ ⚠ ★ ✔ ⚡ ☺) above the line from the moment it is spoken until the line ends. Keywords are listed per clip in `clips.json` (`caption_keywords`).
- Alternatives considered: full-colour emoji (libass can't draw colour-bitmap fonts like Noto Color Emoji; it would need PNG overlays, e.g. Twemoji, CC-BY 4.0, from GitHub releases, plus attribution) — split out as a later roadmap item; emphasising every matching word (too busy); an LLM picking keywords (needs H-004).
- Why: the symbols come from DejaVu Sans (fonts-dejavu-core, already needed for Hebrew), so there is no new download or licence, and everything stays inside one ASS file.
- How to reverse: `write_ass(..., keywords=False, emoji=False)`, or edit the word lists / `SYMBOLS` in `keywords.py`.

## D-008: Karaoke captions as one ASS event per word, auto base direction (2026-11-19)
- Decision: styles `basic` (static), `bold` (default: white, spoken word yellow and 112%), `box` (white on one dark translucent box, spoken word green; ASS BorderStyle 4) and `pop` (uppercase, spoken word pink and 125%). Each caption line becomes one Dialogue event per spoken word, with the whole line visible and the active word wrapped in override tags. The style's Encoding is **-1** so libass picks the paragraph direction from the text. CLI `--caption-style`.
- Alternatives considered: ASS `\k`/`\kf` karaoke tags (fill sweeps left to right, wrong for Hebrew); drawing captions ourselves with Pillow (much more code, loses libass shaping).
- Why: libass already does shaping and bidi. Found while building this: with Encoding 1 the base direction is LTR, so once override tags split a Hebrew line into runs, the runs were drawn in LTR order. A pixel test now checks that the first spoken Hebrew word is drawn at the right end.
- Known limits: word times inside a speech chunk are estimates (D-002), so the highlight can run slightly ahead or behind. Older libass builds without BorderStyle 4 draw `box` with per-word boxes.
- How to reverse: `STYLES` in `captions.py`; `--caption-style basic` gives the old static look.

## D-007: Several people: frame the group if it fits, else follow the active speaker by mouth motion (2026-11-19)
- Decision: faces from YuNet are grouped into tracks by position (greedy nearest match; a track must appear in 25% of samples). One track: follow it. Several tracks whose combined width fits in 90% of the crop: frame the group. Otherwise the active speaker is the track whose lower-face region changes most between samples (1 s moving average), with hysteresis (another track must be 1.5x more active for 0.75 s; the switch is back-dated to when that started). A speaker change is a hard cut for the virtual camera, like a scene cut. New fixture `two_speakers` (two cartoon faces 960 px apart, the speaking one's mouth flickers) with the active speaker as `face_track`.
- Alternatives considered: audio-visual active-speaker models (TalkNet, Light-ASD: weights not reachable here, heavier); speaker diarization from audio alone (no link from voice to face without a model); split screen (kept as a separate option item).
- Why: pure OpenCV/numpy, adds almost nothing to processing time, and matches how real talking mouths behave. On the fixture the crop switches 0.1–0.3 s after the real speaker change.
- Known limits: a listener who nods or laughs a lot can steal the camera; up to 4 tracks considered.
- How to reverse: `detect_subjects()` in `reframe.py`; returning the biggest face per frame restores single-face following.

## D-006: Defer in-sentence filler removal until accurate word timestamps exist (2026-11-19)
- Decision: the M2 item "filler words inside sentences" stays open; the nightly agent moves on to M3 reframing first.
- Alternatives considered: cutting "um"/"so" at the proportional word times we have now (would clip real words, since times inside a VAD chunk are estimates); adding a second ASR model with token timestamps just for alignment (doubles transcription time).
- Why: the proper fix is real word timestamps (faster-whisper / a CTC aligner), which mostly live on huggingface.co (H-001). Reframing had a large, measurable gap (face inside crop 0.65).
- How to reverse: pick the item up next; it is still the first unchecked M2 line in the roadmap.

## D-005: Speaker-following reframe with YuNet + skin-colour fallback, Gaussian camera path (2026-11-19)
- Decision: `clipmaker/reframe.py` samples each clip at 4 fps at 480 px wide, detects faces with **YuNet** (`cv2.FaceDetectorYN`, model `face_detection_yunet_2023mar.onnx`, 230 KB, downloaded at runtime from opencv_zoo via media.githubusercontent.com and checked against a pinned sha256). If YuNet finds faces in fewer than 20% of a clip's samples, it falls back to the largest skin-coloured blob (YCrCb thresholds). The biggest face is followed. Camera path: gaps filled with the nearest detection, zero-lag Gaussian smoothing (sigma 0.5 s), computed **per shot** so it jumps at scene cuts instead of panning, then nudged so the subject box stays fully inside the crop. The path is simplified (Ramer-Douglas-Peucker, 2 px) and fed to ffmpeg's `crop` as a piecewise-linear expression of `t`; the crop runs before the dead-air trims. `--static-crop` turns it off.
- Licenses: OpenCV Apache-2.0; YuNet model MIT (opencv_zoo).
- Alternatives considered: Haar cascades (not shipped with OpenCV 5 wheels, weaker); MediaPipe (heavier, models via Google storage); OpenCV res10 SSD (older, Caffe); a per-frame `sendcmd` file instead of an expression (more moving parts).
- Why: YuNet is tiny, fast on CPU and reachable from the allowlisted network. The skin fallback keeps the synthetic eval fixtures (flat "face" boxes) measurable and helps when no face is visible. Offline smoothing has no lag, unlike a real-time filter.
- Known limits: one subject only (active speaker / split screen is a separate roadmap item); skin fallback can latch onto hands or arms in real footage when no face is ever detected.
- How to reverse: `run_pipeline(reframe=False)` or `--static-crop` restores the centred crop; everything lives in `reframe.py` plus the `xy` argument of `render_clip`.

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
