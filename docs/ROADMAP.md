# Roadmap

The nightly agent ticks items as they're completed and may split, reorder or add
items, with a note in `DECISIONS.md` when the change is significant. Work top-down.

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
- [ ] Visual review of contact sheets in the nightly log

## M5: Web app
- [ ] Upload a video, background job queue, live progress
- [ ] Clip gallery with score, transcript and preview player
- [ ] Edit caption text and trim clip start and end, then re-render
- [ ] Download a single clip or all clips (zip)
- [ ] Database models for projects, jobs and clips, with migrations
- [ ] End-to-end test of the web flow (headless browser) on fixtures

## M6: Polish
- [ ] Aspect ratio options (9:16, 1:1, 16:9)
- [ ] Brand kit: font, colors, logo watermark
- [ ] Performance: parallel rendering, caching transcripts
- [ ] Docker image and docs for self-hosting
- [ ] Basic auth and multiple users

## M7: Deployment (needs the owner)
- [ ] Pick hosting and a managed database. This needs accounts, so it's a NEEDS_HUMAN item. Prepare everything so it is a one-step deploy once the owner provides credentials.
