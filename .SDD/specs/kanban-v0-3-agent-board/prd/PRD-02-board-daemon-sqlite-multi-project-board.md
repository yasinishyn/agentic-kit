---
title: PRD-02 - Board daemon, SQLite store, plug-in contracts and multi-project board
status: todo
updated: 2026-09-28
---

# PRD-02: Board daemon, SQLite store, plug-in contracts and multi-project board

| Field | Value |
|---|---|
| Requirements covered | KB3-FR-02, 03 (amended by Q13), 04, 05, 06, 07 (recording), 24; KB3-NFR-02, 03, 06, 07, 09 |
| ADRs | ADR-002, ADR-003, ADR-007 |
| Depends on | PRD-01 |
| Parallelisable | No — wave 2 |

## Goal
A single per-user daemon with the SQLite store, scoped-token API, streamed events and the daemon/UI plug-in contracts (architecture §3.1–3.2), serving a multi-project board with drag-and-drop; the MCP server ensure-starts it, registers, and falls back to v0.2 local mode.

## Acceptance criteria
- [ ] `daemon.py --ensure`: flock single instance; two concurrent calls → one pid; newer version replaces an idle older daemon; stdio detached; `--stop` refuses with live runs unless `--cancel-runs`; `--open` opens the browser with the UI token in the URL fragment
- [ ] `kanban_db.py` owns all migrations (`user_version` 1): projects, sessions, handoffs, runs (+ partial unique index for live runs per ticket), run_events, approvals, approval_files, ticket_sessions, settings; WAL, `busy_timeout`, connection per thread; run transition function with sticky terminal states; claim/coalesce/supersede/requeue accessors; deleting the DB → recreated, board still complete from markdown
- [ ] Tokens: `client.token` and `ui.token` (0600, `KANBAN_HOME` 0700); scope matrix enforced on every `/api/*`; Host check; no CORS headers; static `/` and `/ui/*` only are unauthenticated
- [ ] CSP exactly as architecture §8; no inline script or `on*=` attribute in `ui/`
- [ ] Events: `GET /api/events` streamed with the Authorization header (fetch/chunked), `board.changed` within 2 s of a markdown change, `handoff.created`; max 32 subscribers
- [ ] Human move endpoint (UI token): guarded `move_ticket`, then hand-off row per `handoff_kind` with supersede/coalesce, publish, and `on_human_move` hooks; actors set server-side
- [ ] Approvals: `kanban_db.record_approval(project, ticket, actor)` is the single writer — `km.approve`, an `approvals` row and an `approval_files` snapshot; endpoints `POST …/approve` (UI token, actor `human (board)`) and `POST …/approve-chat` (client token, actor `human (chat)`, refused by the daemon when the calling session's kind is `headless`)
- [ ] Live sessions: the events handler tracks open subscriptions per session; `ctx.live_sessions(project_id, channel=None)` returns them; a session is live only while its subscription is open
- [ ] Plug-in contracts §3.1 (`register(ctx)`, `on_startup`, `on_human_move`, scopes) and §3.2 (`window.kanban` API, `/ui/modules` discovery) implemented and tested with a sample plug-in fixture
- [ ] UI: project switcher; columns; drag-and-drop plus ← / → buttons; drawer with sub-tasks (collapsed beyond 8), files and plug-in panels; markdown viewer fetched through the API (no data navigations); 10 tickets × 50 PRDs render < 200 ms; focus visible; drag has a keyboard path; `aria-live` region for board changes
- [ ] MCP `server.py`: `--ensure`, `POST /api/sessions {project_root, claude_pid, claude_start_time, kind}`; `KANBAN_NO_DAEMON=1` or unresolved api mismatch → v0.2 local mode + one stderr notice; `kanban_board` prints the board URL without any token
- [ ] `tests/helpers.py`: temp `KANBAN_HOME`, `KANBAN_NO_DAEMON` and a throwaway test daemon; every kanban test uses it (no test touches the real `KANBAN_HOME`)

## Owned files (only these may change)
- `plugins/kanban/scripts/daemon.py` (new)
- `plugins/kanban/scripts/kanban_db.py` (new, all migrations)
- `plugins/kanban/scripts/mdview.py` (new)
- `plugins/kanban/scripts/server.py`
- `plugins/kanban/ui/index.html`, `plugins/kanban/ui/app.js`, `plugins/kanban/ui/board.js`, `plugins/kanban/ui/board.css`, `plugins/kanban/ui/modules/.gitkeep` (new)
- `plugins/kanban/tests/helpers.py`, `plugins/kanban/tests/test_db.py`, `plugins/kanban/tests/test_daemon.py`, `plugins/kanban/tests/test_daemon_auth.py`, `plugins/kanban/tests/fixtures/routes_sample.py` (new)
- `plugins/kanban/tests/test_kanban.py` (isolation + tool-list updates)

## Forbidden files
- Other PRDs' owned files; `.SDD/templates/**`; any SQLite migration outside `kanban_db.py` (PRD-02)

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_db_migrates_and_recovers` | user_version 1, all tables and the live-run unique index; board complete after DB deletion |
| `test_run_terminal_states_sticky` | succeeded → waiting refused |
| `test_ensure_single_instance` | two concurrent --ensure → one pid; version replace only when idle |
| `test_auth_scope_matrix` | no token 401; client token on approve/human-move/file-PUT/runs-start 403, on approve-chat 200 (interactive session); Host evil.com 403; no ACAO header |
| `test_csp_header` | exact CSP string on `/` |
| `test_record_approval_both_actors` | board and chat approvals each write markdown + approvals row + snapshot; approve-chat from a headless session 403 |
| `test_live_sessions` | session live while subscribed, not after disconnect |
| `test_events_stream_auth` | `/api/events` without header 401; with header receives board.changed |
| `test_human_move_handoff_supersede_coalesce` | second unclaimed move supersedes; move to live run's stage coalesces |
| `test_plugin_contract` | fixture routes module registered; failing module skipped and logged |
| `test_mcp_local_mode` | KANBAN_NO_DAEMON=1 → v0.2 behaviour |
| `test_board_url_has_no_token` | kanban_board text contains no token |

Review focus: token leakage (logs, URLs, daemon.json, tool output), DNS rebinding, project id → path mapping only from the registry.

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py' -v
python3 tests/test_install.py
claude plugin validate plugins/kanban && claude plugin validate .
```

## Deliverable
The owned files changed and staged (`git add -- <owned>`), plus a short report: files changed, tests added/passing
(verbatim counts), the suggested commit message `PRD-02: <summary> (KB3-FR-..)`, open issues.
