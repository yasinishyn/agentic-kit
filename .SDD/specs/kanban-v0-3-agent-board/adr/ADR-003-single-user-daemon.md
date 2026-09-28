# ADR-003: Process topology: one board daemon per user

| Field | Value |
|---|---|
| Status | Proposed — architect |
| Date | 2026-09-28 |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-agent-board |

## Context
Today every Claude session starts its own `server.py` with its own board (four were running on the kit owner's Mac on 2026-09-28), so a move in one board is invisible to other sessions and the board dies with the session.

## Options
| Option | For | Against |
|---|---|---|
| **A. One daemon per user; MCP servers and the app are clients (chosen)** | one place for hand-offs, runs, SSE; survives sessions; headless runs have a stable parent | lifecycle and version negotiation |
| B. Daemon inside the Tauri app only | simplest lifecycle | board and hand-offs vanish when the app is closed; CLI-only users lose the board |
| C. Keep per-session servers, share via files | no daemon | polling, races, no atomic claim |

## Decision
`daemon.py --ensure` (idempotent) starts a detached daemon and writes `<KANBAN_HOME>/daemon.json`; health via `GET /api/health` → `{api: 1, pid, version}`. MCP servers ensure-start it; mismatched `api` → local mode (today's behaviour). The app supervises the same daemon and never starts a second one.

## Consequences
+ consistent state. − a background process the user may not expect: the app shows its status and offers Stop.
