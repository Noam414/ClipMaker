# Roadmap

The nightly agent ticks items as they're completed and may split, reorder or add
items, with a note in `DECISIONS.md` when the change is significant. Work top-down.

**Priority (owner, 2026-11-22): next run starts with the YouTube bot-check research item in M5.5** (search GitHub for
tools that get yt-dlp past YouTube's "confirm you're not a bot" block on server IPs). Then continue as below.

**Priority (owner, 2026-11-21):** first M5.5 (link to clips in ≤ 5 minutes), then the M7 preparation
items (ARM check, image, deploy kit, guardrails), then the remaining unchecked items top-down.
M8 and M9 are later: start them only after M7 is deployed.

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
- [x] Speed eval: a 10-min synthetic fixture, a `time_to_all_clips_s` metric with per-stage timings, and the value extrapolated to 60 min; target ≤ 300 s (`long_talk`, D-015)
- [x] Reorder the pipeline: audio first (extract with `-vn`), transcribe and select clips from the audio alone; scene detection, face tracking and render only on the selected ranges (+2 s padding) (D-015)
- [x] Faster transcription: benchmark Whisper tiny vs base and other sherpa-onnx models for English; choose by WER vs speed (Hebrew stays on Whisper) — Moonshine base for English, language ID when none is given (D-016)
- [x] Decode VAD chunks in parallel workers (D-021: 2 single-threaded recognizers ~1.9x faster than 1 with 2 threads; `decode_streams` batching gave nothing). Re-measure on ARM in M7
- [x] Render the clips in parallel (one ffmpeg per core) with a faster x264 preset; check quality on the contact sheets (D-017)
- [x] Results appear as they come: each clip shows up in the gallery as soon as it's rendered (`on_clip` callback, `clips_ready` in `/api/jobs/<id>`)
- [x] URL import of direct media links (`http(s)://…/video.mp4`) in the CLI: size and length limits, block private/internal addresses incl. on redirects (SSRF) (D-018)
- [x] URL import in the web app: a "paste a link" field next to upload; the download runs in the job with progress (`POST /api/projects/link`; YouTube/Twitch links get a clear "not yet" until the item below)
- [x] Pin the checked IP for link downloads to close DNS rebinding (before the public server goes live) (D-022)
- [x] HLS `.m3u8` links: every playlist/segment URL fetched by us with the same SSRF rules; ffmpeg reads local files only (`hls.py`, D-024)
- [x] Direct links, part 1: a loopback relay (`relay.py`, D-023) so ffmpeg reads the link only through the checked, pinned opener; `relay.fetch_audio` (audio track only to disk) and `relay.fetch_section` (Range seek, frame-accurate) with tests against a local Range server that counts bytes
- [x] ~~Direct links, part 2/3 (sections mode for single-file links)~~ dropped after measuring (D-024): audio-only from an interleaved MP4 reads 100 % of the file, so it would cost the full file plus the sections. Single-file links keep the plain checked download
- [x] Audio-only for HLS sources (separate audio rendition, else the lowest variant) and only the selected clips' segments (D-024)
- [ ] DASH `.mpd` links (same approach as HLS; only if real sources need it)
- [x] **(owner request 2026-11-22, do first)** YouTube bot checks on server IPs: search GitHub (`gh api search/repositories`) for maintained tools that work with yt-dlp, e.g. PO-token provider plugins (such as `bgutil-ytdlp-pot-provider`), yt-dlp's cookie support (cookies from an account the owner controls), an optional proxy setting. Compare stars/activity/licence, pick one, record it in `DECISIONS.md`, add it behind config in the fetcher module, test it against the local fake site, and put whatever needs the owner (cookies, proxy account) in `NEEDS_HUMAN.md`. Out of scope: getting around the agent VM's own network block on youtube.com — that's only opened by the owner (H-006). Done: `platforms.py`, `clipmaker platform-check`, D-019, H-007
- [x] YouTube and Twitch links (owner decision H-005, D-014) through yt-dlp: audio-only format first, then only the selected sections (`--download-sections`); Twitch VODs and clips; a "I own or have the rights to this video" checkbox before processing (D-020; CLI `--rights`; tested against a local site only, since youtube.com is blocked in the VM, H-006)
- [x] Platform-link fallbacks: clear error messages when a platform blocks the server ("upload the file instead"), retries, and a nightly-safe test with a local fake site (youtube.com/twitch.tv are blocked in the agent's VM) (D-025)

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
- [x] ARM check: install and run the tests on linux/arm64 (sherpa-onnx, opencv, onnx wheels); a CI job that builds the Docker image for arm64 (native arm64 runners in CI, D-026)
- [x] Multi-arch Docker image (amd64 + arm64): one arch-neutral Dockerfile, built and smoke-tested natively on both in CI; the server builds it itself, no registry (D-027)
- [x] Deploy kit in `deploy/oracle/`: production compose file, Caddy for free automatic HTTPS, a setup script for a fresh Ubuntu instance, backups of the database (D-028; `clipmaker backup`; CI starts the stack on amd64 + arm64)
- [x] Free-tier guardrails, part 1: auto-delete projects 24 h after upload (`CLIPMAKER_RETENTION_HOURS`), a free-disk floor that deletes the oldest projects first and refuses new jobs when still full (`CLIPMAKER_MIN_FREE_GB`), the deletion time shown in the gallery (D-029). Max length (60 min) and upload size already exist (fetch limits, `CLIPMAKER_MAX_UPLOAD_MB`)
- [x] Free-tier guardrails, part 2: per-visitor daily limit (`CLIPMAKER_DAILY_LIMIT`, per browser cookie **or** IP behind Caddy), visible queue position ("2 videos ahead of you"), and private projects (`CLIPMAKER_PRIVATE`): the home page lists only the visitor's own projects and other visitors get 404 on project pages, clips, zips and job status (D-030)
- [x] Free-tier guardrails, part 3: max length for uploaded files (`CLIPMAKER_MAX_UPLOAD_MINUTES`, the kit sets 60; checked with ffprobe at the start of the job, links were already capped) (D-029)
- [x] Step-by-step guide in H-002 so the owner deploys with one command (`sudo deploy/oracle/setup.sh`)
- [ ] Smoke test against the live site (after H-002 is done): `scripts/smoke_live.py <url>` uploads a fixture and waits for clips

## M8: Publish to social accounts (later, after M7; D-014)
- [ ] User accounts (builds on M6 "basic auth and multiple users")
- [ ] Connect a YouTube account (Google OAuth, YouTube Data API) and upload a clip as a Short, with title, description and privacy setting
- [ ] Connect a TikTok account (TikTok Content Posting API) and post a clip
- [ ] Tokens stored encrypted, disconnect button, per-platform quota handling
- [ ] NEEDS_HUMAN: developer apps, OAuth consent screen, and the platforms' app reviews/audits (until approved, posts may be private-only or limited to test users)

## M9: Subscriptions and automation (later, after M8; D-014)
- [ ] Plans and limits (free vs paid tiers: video length, clips per month, watermark)
- [ ] Payments through a payment provider (NEEDS_HUMAN: business details, account)
- [ ] Automation for the top tier: watch a YouTube channel or Twitch streamer, make clips from every new video/VOD automatically, and optionally auto-post the best ones on a schedule to the connected accounts
- [ ] Capacity: paid tiers need paid servers; plan the move beyond the single free server
