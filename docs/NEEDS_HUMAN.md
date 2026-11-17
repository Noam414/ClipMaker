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

## Resolved
(none yet)
