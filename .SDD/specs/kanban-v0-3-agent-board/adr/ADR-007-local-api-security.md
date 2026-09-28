# ADR-007: Local API security: token + Host check; terminal only over Tauri IPC

| Field | Value |
|---|---|
| Status | Accepted — residual risk Decided by kit owner, 2026-09-28 (Q12) |
| Date | 2026-09-28 |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-agent-board |

## Context
The daemon adds endpoints that start processes and edit files; the app adds a terminal. A web page in the user's browser must not reach them.

## Options
| Option | For | Against |
|---|---|---|
| A. Keep v0.2 (Host check + `X-Kanban` header) | simple | GETs unauthenticated; not enough once runs can be started |
| **B. Bearer token on every `/api/*`, Host check, no CORS; terminal via Tauri IPC only (chosen)** | closes CSRF/rebinding; RCE surface not on HTTP | token distribution to UI |
| C. Unix domain socket | no TCP exposure | WebView cannot fetch a UDS; breaks browser mode |

## Decision
Two scoped tokens in `KANBAN_HOME` (0700 dir, 0600 files): `client.token` for the MCP server and hooks (read, register, claim/heartbeat/finish, `claude` moves, `kanban_approve`), `ui.token` for human actions (human moves, approve endpoint, file PUT, run start/stop). The Tauri shell holds the UI token in memory and injects it on the daemon origin only; browser mode is opened by `daemon.py --open` with the token in the URL fragment (kept in `sessionStorage`). Event streams use fetch with the Authorization header (no `EventSource`, no tokens in URLs). CSP `script-src 'self'`, no inline scripts. The kit guard hook denies reading `ui.token` and Bash HTTP calls to approve/move/file/runs endpoints. Residual risk (a same-user agent working around these) accepted by the owner with detection (Q12).

## Consequences
+ web pages cannot reach the API; Claude's normal tools cannot act as human. − browser mode is opened by a command the user runs; `kanban_board` prints the URL without a token.
