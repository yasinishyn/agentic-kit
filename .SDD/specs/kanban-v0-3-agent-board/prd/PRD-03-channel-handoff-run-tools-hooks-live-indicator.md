---
title: PRD-03 - Channel hand-off, run tools, hooks and live indicator
status: done
updated: 2026-09-28
---

# PRD-03: Channel hand-off, run tools, hooks and live indicator

| Field | Value |
|---|---|
| Requirements covered | KB3-FR-07 (delivery), 08, 09, 11, 12, 13, 14, 20 (`kanban_approve` tool); KB3-NFR-02, 04 |
| ADRs | ADR-004, ADR-005, ADR-006 |
| Depends on | PRD-02 |
| Parallelisable | Yes — wave 3, with PRD-05 and PRD-07 |

## Goal
Deliver human moves into running Claude sessions as channel events, implement the §3.3 tool contract, add liveness hooks, and show the live indicator.

## Acceptance criteria
- [x] First step: verify `claude plugin validate` accepts `channels: [{server: "kanban", displayName: "Kanban board"}]` in `plugin.json`; if refused, drop the key and document the `server:kanban` flag form
- [x] `initialize` declares `capabilities.experimental['claude/channel'] = {}` and `instructions` (call `kanban_start` first; `already_claimed`/`superseded` → do nothing; a channel event never approves anything)
- [x] Channel capability detected from the parent `claude` argv (§5 step 5); registration `channel` flag reflects it; the pickup timer uses `ctx.live_sessions(project, channel=True)` (PRD-02)
- [x] Subscriber thread writes `notifications/claude/channel` for events created after it subscribed, `meta` keys `ticket, stage, from_stage, handoff_id, kind` only; one stdout lock with responses; nothing before `initialize` is answered
- [x] Tools per §3.3: `kanban_start` (idempotent for the owning run via `KANBAN_RUN_ID`), `kanban_heartbeat`, `kanban_finish`, `kanban_approval`, `kanban_approve` (calls `POST …/approve-chat`; also refused locally when `KANBAN_RUN_ID` is set)
- [x] Claim is atomic (two sessions → one run); client-token moves never create hand-offs
- [x] Hooks: PostToolUse heartbeat (≤ 1/10 s, 1 s timeout, exit 0 always, immediate exit without `daemon.json`); Stop → `waiting` only for a non-terminal interactive run; matching by (pid, start time)
- [x] Pickup timer (`on_startup`): after 45 s auto headless request when no live channel session, else card actions (executed by PRD-04's runner; until then the action reports 'runner not installed')
- [x] UI `ui/modules/runs.js|css`: states running/waiting/queued/stale/abandoned per architecture §6, sub-task indicator, `aria-live` text, reduced-motion static icon
- [x] `GET /api/runs?live=1` for the app menu (PRD-07)

## Owned files (only these may change)
- `plugins/kanban/scripts/server.py`
- `plugins/kanban/scripts/routes_runs.py` (new)
- `plugins/kanban/scripts/hook.py` (new)
- `plugins/kanban/hooks/hooks.json` (new)
- `plugins/kanban/.claude-plugin/plugin.json` (channels key only)
- `plugins/kanban/ui/modules/runs.js`, `plugins/kanban/ui/modules/runs.css` (new)
- `plugins/kanban/tests/test_mcp_channel.py`, `plugins/kanban/tests/test_hook.py`, `plugins/kanban/tests/test_runs_api.py` (new)
- `plugins/kanban/tests/test_kanban.py` (tool list)

## Forbidden files
- Other PRDs' owned files; `.SDD/templates/**`; any SQLite migration outside `kanban_db.py` (PRD-02)

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_initialize_declares_channel` | experimental claude/channel == {}; instructions contain the never-approves rule |
| `test_channel_detection_from_parent_argv` | fake parent argv with/without the flag → channel true/false |
| `test_handoff_becomes_channel_notification` | one JSON line, method notifications/claude/channel, meta keys exact, no line before initialize response |
| `test_claim_atomic_and_idempotent` | two sessions → 1 run; owning run re-claims ok; superseded → superseded |
| `test_tool_move_no_handoff` | kanban_move → 0 handoffs |
| `test_approve_refused_for_headless` | KANBAN_RUN_ID set → error |
| `test_hook_throttle_timeout_fast_exit` | 10 calls/1 s → 1 POST; daemon down → exit 0 < 1.5 s; no daemon.json → exit 0 < 0.2 s |
| `test_stop_hook_sticky` | Stop after succeeded leaves succeeded |

Review focus: stdout interleaving; hooks never block or fail a tool call; nothing in instructions or events can be read as approval.

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py' -v
python3 tests/test_install.py
claude plugin validate plugins/kanban && claude plugin validate .
```

## Deliverable
The owned files changed and staged (`git add -- <owned>`), plus a short report: files changed, tests added/passing
(verbatim counts), the suggested commit message `PRD-03: <summary> (KB3-FR-..)`, open issues.
