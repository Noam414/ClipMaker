# Roadmap

The nightly agent ticks items as they're completed and may split, reorder or add
items, with a note in `DECISIONS.md` when the change is significant. Work top-down.

**Priority (owner, 2026-11-21):** first M5.5 (link to clips in ≤ 5 minutes), then the M7 preparation
items (ARM check, image, deploy kit, guardrails), then the remaining unchecked items top-down.

## M0: Foundation
- [x] Choose the stack (backend, frontend, job queue, database) and record it in `DECISIONS.md`
- [x] Project skeleton, dependency files, `.env.example`, a one-command local run that works on Windows
- [x] Test runner with a first passing test
- [x] CI that runs tests on pull requests. If pushing `.github/workflows/` is rejected, keep the file in `docs/ci/` and add an item to `NEEDS_HUMAN.md`.
- [x] Eval harness skeleton (`scripts/eval.py`) that reads `tests/fixtures/videos/manifest.json` and writes `eval/results/latest.json`

## M1: Core pipeline (CLI)
- [x] Transcription with word timestamps (English and Hebrew), using a CPU-friendly model with runtime download and caching
- [x] Sentence segmentation and candidate clips of 15–60 s on sentence boundaries
- [x] Cut clips accurately with re-encoding, not keyframe-snapped
- [x] Center-crop to 1080x1920, leaving vertical inputs uncropped
- [x] Burned-in captions, basic style
- [x] `clipmaker <input.mp4> --out <dir>` produces clips and a `clips.json` with timings, text and scores
- [x] Eval metrics: WER, boundary accuracy, output validity, speed

## M2: Smart highlights
- [x] Highlight scoring heuristics: hook phrases, questions, numbers, emotion words, completeness, pacing
- [x] Optional LLM scoring behind an interface, off by default; the API key is a NEEDS_HUMAN item
- [x] Dead-air removal: long pauses shortened, filler-only chunks ("um") dropped
- [ ] Filler words inside sentences ("um", "so"): needs accurate word timestamps (forced alignment or a model with token times). Deferred, see D-006
- [x] Eval: share of clips starting on a hook, dead air remaining

## M3: Reframing
- [x] Face and speaker detection (CPU), with a smoothed camera path and no jitter (YuNet + skin fallback, D-005)
- [x] Scene-cut detection (ffmpeg scdet), reported in `clips.json`
- [x] Reset the camera path at scene cuts (with face tracking)
- [x] Eval fixture with a real-looking face, so YuNet itself is exercised (not only the skin fallback)
- [x] Multiple speakers: follow the active speaker (lower-face motion), or frame the group when everyone fits (D-007)
- [ ] Split-screen layout option for two speakers who don't fit one crop
- [ ] Active speaker from audio too (voice/diarization), for faces that barely move
- [x] Eval: face-inside-crop rate, scene-cut precision and recall

## M4: Captions and effects
- [x] Animated word-by-word captions (karaoke highlight), 3+ style presets (D-008)
- [x] Correct Hebrew right-to-left shaping, word order and punctuation (pixel test in `test_captions.py`)
- [x] Keyword emphasis (color or scale) and optional emoji (symbol glyphs, D-009)
- [ ] Full-colour emoji as PNG overlays (e.g. Twemoji, CC-BY, with attribution)
- [x] Progress bar and hook title card in the first 3 s (D-010)
- [x] Zoom punch-ins on emphasis (D-011)
- [x] Eval: account for punch-in zoom in the face-inside-crop metric
- [x] Visual review of contact sheets in the nightly log (a fixed section in the log template)

## M5.5: Link to clips in under 5 minutes (owner request 2026-11-21, D-013) — **do this before the rest of M5**
Goal: from a pasted link to all clips ready in **≤ 5 minutes** for a video of up to 60 minutes, on the
Oracle free server (2 ARM cores, 12 GB). Flow: download **audio only** → transcribe → pick clips from
the text → download **only the selected video ranges** → reframe and render. Today's baseline (2 x86
cores): ~26 s of processing per input minute, i.e. ~26 min for a 60-min video; transcription ~7 s/min,
scene detection ~4 s/min (on the whole video), render ~10 s per clip.
- [ ] Speed eval: a 10-min synthetic fixture, a `time_to_all_clips_s` metric with per-stage timings, and the value extrapolated to 60 min; target ≤ 300 s
- [ ] Reorder the pipeline: audio first (extract with `-vn`), transcribe and select clips from the audio alone; scene detection, face tracking and render only on the selected ranges (+2 s padding)
- [ ] Faster transcription: benchmark Whisper tiny vs base, other sherpa-onnx models for English, and VAD chunks in parallel on all cores; choose by WER vs speed (Hebrew stays on Whisper)
- [ ] Render the clips in parallel (one ffmpeg per core) with a faster x264 preset; check quality on the contact sheets
- [ ] Results appear as they come: each clip shows up in the gallery as soon as it's rendered
- [ ] URL import of direct media links (`http(s)://…/video.mp4`, HLS `.m3u8`): size and length limits, block private/internal addresses (SSRF)
- [ ] Audio-only fetch from the link: for HLS/DASH sources with a separate audio track, download only the audio; for a single MP4 file, stream the audio with ffmpeg without saving the video
- [ ] Fetch only the selected video ranges from the link (ffmpeg `-ss`/`-to` over HTTP range requests); eval test with a local HTTP server that counts bytes served
- [ ] Platform links (YouTube etc.): only after the owner decides H-005

## M5: Web app
- [x] Upload a video, background job queue, live progress (polling, D-012)
- [x] Clip gallery with score, transcript and preview player (`/projects/<id>`)
- [ ] Edit caption text and trim clip start and end, then re-render
- [x] Download a single clip or all clips (zip)
- [x] Database models for projects, jobs and clips, with migrations (Alembic, D-012)
- [x] End-to-end test of the web flow (headless browser) on fixtures (`tests/test_e2e_web.py`, Playwright)

## M6: Polish
- [ ] Aspect ratio options (9:16, 1:1, 16:9)
- [ ] Brand kit: font, colors, logo watermark
- [ ] Performance: caching transcripts (parallel rendering moved to M5.5)
- [ ] Docs for self-hosting (the image itself is in M7)
- [ ] Basic auth and multiple users

## M7: Deployment on the Oracle Cloud free tier (D-013, H-002)
- [x] Pick hosting: Oracle Cloud Always Free, Ampere A1 (2 ARM cores, 12 GB RAM, 200 GB disk), SQLite on the server's disk to start
- [ ] ARM check: install and run the tests on linux/arm64 (sherpa-onnx, opencv, onnx wheels); a CI job that builds the Docker image for arm64
- [ ] Multi-arch Docker image (amd64 + arm64)
- [ ] Deploy kit in `deploy/oracle/`: production compose file, Caddy for free automatic HTTPS, a setup script for a fresh Ubuntu instance, backups of the database
- [ ] Free-tier guardrails: max video length (60 min) and upload size, per-visitor daily limit, visible queue position, auto-delete files after 24 h, disk-usage cap
- [ ] Step-by-step guide in H-002 so the owner deploys with one command, and a smoke test against the live site
