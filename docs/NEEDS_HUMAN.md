# Needs the owner

Things the nightly agent can't do alone. Each item says exactly what to do. The agent
re-checks open items every night and moves resolved ones to the bottom.

## Open

### H-001: (Optional) Allow Hugging Face in the cloud environment
- **Why:** Most open speech and vision models are hosted on huggingface.co, which the default network allowlist blocks. The agent can work around this with models published on GitHub releases, but allowing Hugging Face gives it far more choice.
- **Steps:** "ClipMaker nightly" is the name of the scheduled task, not of the environment. At claude.ai/code, open that scheduled task and note which environment it runs in (if none is named, it is your default environment, often called "Default"). Then open the environment selector, edit that environment, set **Network access** to **Custom**, check **Also include default list of common package managers**, and add:
  ```
  huggingface.co
  *.huggingface.co
  *.hf.co
  ```
- **Agent check:** `curl -sI https://huggingface.co` returns 200 or 3xx.

### H-002: Oracle Cloud free server for the live website (M7, D-013)
- **Why:** the website must run on a server so visitors don't depend on their own computers. Oracle's "Always Free" tier gives one 2-core ARM server with 12 GB RAM and 200 GB disk at no cost; the agent can't sign up or deploy for you.
- **When:** wait until the agent says the deploy kit is ready (expected in about 1–1.5 weeks, after M5.5 and the M7 preparation). A server that sits idle for about a week may be reclaimed by Oracle.
- **Steps (~20 min):**
  1. Sign up at cloud.oracle.com/free (a credit card is needed for identity verification; Always Free resources are not charged). Pick a home region close to your users; it can't be changed later.
  2. Compute → Instances → Create instance: image **Ubuntu 24.04**, shape **VM.Standard.A1.Flex** with **2 OCPU / 12 GB**. Upload or generate an SSH key and keep the private key. If it says "out of capacity", retry later or in another availability domain.
  3. Networking → the instance's subnet → Security list: add ingress rules for TCP **80** and **443** from 0.0.0.0/0.
  4. Write here, under "Resolved", that the server exists and its public IP (never the SSH key).
- **Later:** when the deploy kit (`deploy/oracle/`) is ready, the agent will add the one command to run on the server, and how to get a free HTTPS address.
- **Agent check:** this section says the server exists.

### H-003: (Optional) A short Hebrew speech recording for the eval
- **Why:** the Hebrew test video is voiced by espeak-ng, which pronounces Hebrew so poorly that no speech model can transcribe it (WER ~0.97), so Hebrew transcription quality can't be measured.
- **Steps:** record yourself (or use audio you own the rights to) reading 4–6 Hebrew sentences, about 20–40 s. Commit it as `tests/fixtures/audio/hebrew_owner.m4a` (under ~2 MB) with the exact text in `tests/fixtures/audio/hebrew_owner.txt`, one sentence per line. The agent will turn it into a fixture with ground truth.
- **Agent check:** the two files exist on `main` or `claude/nightly`.

### H-004: (Optional) Anthropic API key for LLM clip ranking
- **Why:** ClipMaker can re-rank its top candidate clips with an LLM (`clipmaker/llm_scoring.py`). It is off by default and the heuristic ranking works without it. When on, clip transcripts are sent to Anthropic's API, which costs a little per video.
- **Steps:** create a key at console.anthropic.com, then locally put `CLIPMAKER_LLM_SCORING=1` and `ANTHROPIC_API_KEY=...` in `.env`. For the nightly eval, add `ANTHROPIC_API_KEY` as an environment variable/secret of the cloud environment **and** allow `api.anthropic.com` in its network settings; tell the agent in `NEEDS_HUMAN.md` that it's there.
- **Agent check:** `ANTHROPIC_API_KEY` is set in the environment.

### H-006: (Optional) Let the nightly agent reach YouTube and Twitch
- **Why:** youtube.com and twitch.tv are blocked in the agent's cloud environment, so it can only test platform links against a fake local site. Opening them lets it check real links nightly.
- **Steps:** same place as H-001 (the scheduled task's environment → Network access → Custom, keep the default package-manager list), add:
  ```
  youtube.com
  *.youtube.com
  *.googlevideo.com
  *.ytimg.com
  twitch.tv
  *.twitch.tv
  *.ttvnw.net
  *.jtvnw.net
  ```
- **Agent check:** `curl -sI https://www.youtube.com` returns 200 or 3xx.

### H-007: (When the server is up) Help ClipMaker past YouTube's "confirm you're not a bot" (D-019)
- **Why:** YouTube often blocks downloads from cloud servers like the Oracle one. ClipMaker can use three helpers; the first is free and needs no account, the other two need you.
- **Step 1 — nothing to do now:** the M7 deploy kit will run the free PO-token provider (Docker image `brainicism/bgutil-ytdlp-pot-provider`) next to ClipMaker and set `CLIPMAKER_YTDLP_POT_URL`. Try YouTube links on the live site first with only this.
- **Step 2 — only if links still fail with the bot message:** create a **separate** Google account just for this (YouTube may flag or ban it — never use your main account). Sign in to YouTube with it in a private browser window, export cookies for youtube.com in Netscape format (e.g. with the open-source "Get cookies.txt LOCALLY" extension), then close that window without signing out. Copy the file to the server (e.g. `/data/secrets/youtube-cookies.txt`, readable only by ClipMaker), set `CLIPMAKER_YTDLP_COOKIES` to that path in the server's `.env`, and **never commit it to git**. Cookies expire after weeks or months; repeat when the bot message returns.
- **Step 3 — last resort, costs money:** a residential proxy service; put its URL in `CLIPMAKER_YTDLP_PROXY`. Tell the agent here before buying anything.
- **Check:** on the server, `clipmaker platform-check` should print no `problem:` lines.
- **Agent check:** this section says which steps were done.

## Resolved

### H-005: Which links should the site accept? — resolved 2026-11-21
- **Owner's answer:** direct links **plus YouTube and Twitch** (D-014). Later: connecting YouTube/TikTok accounts to publish clips (M8) and automation for a top paid tier (M9).

