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
- [ ] `clipmaker <input.mp4> --out <dir>` produces clips and a `clips.json` with timings, text and scores
- [ ] Eval metrics: WER, boundary accuracy, output validity, speed

## M2: Smart highlights
- [ ] Highlight scoring heuristics: hook phrases, questions, numbers, emotion words, completeness, pacing
- [ ] Optional LLM scoring behind an interface, off by default; the API key is a NEEDS_HUMAN item
- [ ] Dead-air and filler-word removal ("um", "so", long pauses)
- [ ] Eval: share of clips starting on a hook, dead air remaining

## M3: Reframing
- [ ] Face and speaker detection (CPU), with a smoothed camera path and no jitter
- [ ] Scene-cut detection that resets the camera path at cuts
- [ ] Multiple speakers: follow the active speaker or use split screen
- [ ] Eval: face-inside-crop rate, scene-cut precision and recall

## M4: Captions and effects
- [ ] Animated word-by-word captions (karaoke highlight), 3+ style presets
- [ ] Correct Hebrew right-to-left shaping, word order and punctuation
- [ ] Keyword emphasis (color or scale) and optional emoji
- [ ] Zoom punch-ins on emphasis, progress bar, hook title card in the first 3 s
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
