# ADR-008: Embedded terminal: Rust portable-pty + vendored xterm.js

| Field | Value |
|---|---|
| Status | Proposed — architect |
| Date | 2026-09-28 |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-agent-board |

## Context
The owner asked for an embedded terminal (also the easiest way to start a channel-enabled `claude`).

## Options
| Option | For | Against |
|---|---|---|
| **A. Rust `portable-pty` in the Tauri shell, xterm.js UI, Tauri events (chosen)** | never on HTTP; native PTY; resize | Rust code |
| B. Python `pty` in the daemon over SSE + POST | one language | RCE endpoint on HTTP; latency |
| C. Open Terminal.app | trivial | not embedded |

## Decision
Commands `term_open(project, preset)`, `term_write`, `term_resize`, `term_close`; presets: shell, `claude --dangerously-load-development-channels plugin:kanban@agentic-kit`. `xterm.js` (MIT) vendored under `ui/vendor/` with its licence in `LICENSES/`; download needs the owner's permission during Developer.

## Consequences
+ secure by construction. − terminal only in the app, not in browser mode.
