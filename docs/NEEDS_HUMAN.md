# Needs the owner

Things the nightly agent can't do alone. Each item says exactly what to do. The agent
re-checks open items every night and moves resolved ones to the bottom.

## Open

### H-001: (Optional) Allow Hugging Face in the cloud environment
- **Why:** Most open speech and vision models are hosted on huggingface.co, which the default network allowlist blocks. The agent can work around this with models published on GitHub releases, but allowing Hugging Face gives it far more choice.
- **Steps:** at claude.ai/code, open the environment selector, edit the environment used by the "ClipMaker nightly" scheduled task, set **Network access** to **Custom**, check **Also include default list of common package managers**, and add:
  ```
  huggingface.co
  *.huggingface.co
  *.hf.co
  ```
- **Agent check:** `curl -sI https://huggingface.co` returns 200 or 3xx.

### H-002: Hosting and database for the live website (later, M7)
- **Why:** Deploying needs accounts and possibly payment. Until then everything runs locally, and inside the VM for tests.
- **Steps:** none yet. When M5 is done, the agent will add a recommendation and exact steps here.

### H-003: (Optional) A short Hebrew speech recording for the eval
- **Why:** the Hebrew test video is voiced by espeak-ng, which pronounces Hebrew so poorly that no speech model can transcribe it (WER ~0.97), so Hebrew transcription quality can't be measured.
- **Steps:** record yourself (or use audio you own the rights to) reading 4–6 Hebrew sentences, about 20–40 s. Commit it as `tests/fixtures/audio/hebrew_owner.m4a` (under ~2 MB) with the exact text in `tests/fixtures/audio/hebrew_owner.txt`, one sentence per line. The agent will turn it into a fixture with ground truth.
- **Agent check:** the two files exist on `main` or `claude/nightly`.

### H-004: (Optional) Anthropic API key for LLM clip ranking
- **Why:** ClipMaker can re-rank its top candidate clips with an LLM (`clipmaker/llm_scoring.py`). It is off by default and the heuristic ranking works without it. When on, clip transcripts are sent to Anthropic's API, which costs a little per video.
- **Steps:** create a key at console.anthropic.com, then locally put `CLIPMAKER_LLM_SCORING=1` and `ANTHROPIC_API_KEY=...` in `.env`. For the nightly eval, add `ANTHROPIC_API_KEY` as an environment variable/secret of the cloud environment **and** allow `api.anthropic.com` in its network settings; tell the agent in `NEEDS_HUMAN.md` that it's there.
- **Agent check:** `ANTHROPIC_API_KEY` is set in the environment.

## Resolved
(none yet)
