# ADR-004: Hand-off delivery: channel first, headless fallback

| Field | Value |
|---|---|
| Status | Accepted — Decided by kit owner, 2026-09-28 (OQ Q02, Q05) |
| Date | 2026-09-28 |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-agent-board |

## Context
A human move on the board must start Claude on the next stage. Channels push into a running session but are a research preview, fire-and-forget, and not available to Code-tab sessions (OQ Q04 evidence).

## Options
| Option | For | Against |
|---|---|---|
| **A. Channel broadcast + atomic claim + headless fallback (chosen)** | uses the open session's context when available; still works with no session | two paths to test |
| B. Headless only (paperclip `claude_local`) | one path, always works | ignores the user's open session; parallel edits in one checkout |
| C. Poll tool (`kanban_next`) that Claude calls | no preview feature | Claude must be prompted to poll; no push |

## Decision
Triggers per Q05: forward moves into discovery/architect/developer/qa/demo/e2e → `start`; backward moves into a working stage → `rework`; approval/done → none; actor ≠ human → none. Every registered session of the project receives the channel event; `kanban_start(handoff_id)` claims atomically. No claim in 45 s → auto headless if no channel-capable session is registered, else card actions "Run headless" / "Copy prompt". Headless argv per Q06: `claude -p <template> --output-format stream-json --verbose --permission-mode acceptEdits [--resume <id>]`, cwd = project root, own process group.

## Consequences
+ works for CLI, Code tab (via fallback) and no session. − channel sessions need `--dangerously-load-development-channels plugin:kanban@agentic-kit` during the preview; `plugin.json` gains `channels: [{server: "kanban"}]`.
