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

## D-000: Autonomous nightly development on `claude/nightly` (2026-11-17)
- Decision: A scheduled Claude Code task runs every night, develops on branch `claude/nightly`, and keeps one PR open to `main`. The owner merges when they choose. All technical choices are delegated to the agent.
- Alternatives considered: pushing straight to `main`; opening a new PR every night.
- Why: Work builds up from night to night even if the PR isn't merged right away, and `main` stays under the owner's control.
- How to reverse: Edit the scheduled task's prompt and the "Nightly workflow" section of `CLAUDE.md`.
