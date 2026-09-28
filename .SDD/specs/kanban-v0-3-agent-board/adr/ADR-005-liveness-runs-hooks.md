# ADR-005: Live indicator: runs + heartbeats from tools and plugin hooks

| Field | Value |
|---|---|
| Status | Proposed — architect |
| Date | 2026-09-28 |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-agent-board |

## Context
The loader must reflect real work. Frontmatter status alone is stale in practice (lead-hunt: 0 of 313 PRDs `doing` while 9 of 10 tickets are in Developer).

## Options
| Option | For | Against |
|---|---|---|
| A. Frontmatter `status: doing` only | no new moving parts | stale; no notion of "active now" |
| B. MCP tool calls only (`kanban_start/heartbeat/finish`) | explicit | long silent stretches look dead; relies on Claude remembering |
| **C. Runs + tool calls + PostToolUse/Stop hooks + TTL (chosen)** | accurate "working / waiting for you / stale"; no reliance on memory | hooks add a tiny cost per tool call |

## Decision
Hooks (`hooks/hooks.json`) run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/hook.py`, throttled to one POST per 10 s per session with a 1 s timeout, always exit 0. Runs are matched by the `claude` process id (MCP server registers `os.getppid()`; hook sends its ancestor pids). Stale after 900 s.

## Consequences
+ honest indicator. − the plugin now ships hooks (README said "no hooks"; updated).
