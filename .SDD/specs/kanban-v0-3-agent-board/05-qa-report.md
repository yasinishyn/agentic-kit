# Kanban v0.3 — QA report

| Run | Date | Branch · base commit · staged/uncommitted |
|---|---|---|
| QA | 2026-09-28 | `main` · base `213d2de` (spec) · feature committed by the developer as `cd37ffd`; QA additions (abuse regression test) uncommitted |

## 1. Test results (verbatim counts, vs the baseline)
| Suite | Baseline `213d2de` | Now |
|---|---|---|
| kanban (`python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py'`) | `Ran 5 tests … OK` | `Ran 129 tests in 120.112s` / `OK` (after QA fixes) + `test_abuse.py`: `Ran 1 test … OK` |
| kanban, repeated | — | 20× loop before QA fixes `fails=0/20` (114 tests, load 6–20); 10× loop after QA fixes `fails=0/10` (129 tests) |
| installer (`python3 tests/test_install.py`) | `Ran 7 tests … OK` | `Ran 11 tests … OK` |
| guard (`bash .claude/hooks/test_guard_bash.sh`) | `passed=39 failed=0` | `passed=62 failed=0` |
| Tauri (`cargo test`) | — | `test result: ok. 15 passed; 0 failed` |
| `claude plugin validate plugins/kanban` / `.` | passed | `✔ Validation passed` ×2 |
| End-to-end happy flow (move → channel → claim → live run → gate → finish), throwaway daemon | — | `happy flow: 13/13 passed` |

Timing note: `test_hook_throttle_timeout_fast_exit` failed 2/6 runs at load average ~125 (a macOS VM plus a Rust build);
0 failures at normal load. Root cause (full process-table scan in `hook.py`) fixed in B5.

## 2. Code review findings (/code-review, high)
| # | Severity | Finding | File:line | Status |
|---|---|---|---|---|
| 1 | High | In-flight tickets without a README approval record could not move inside execution | `kanban_md.py:214` | fixed (B1, Q14) |
| 2 | High | Local mode had no path into Developer (`kanban_approve` refused) | `server.py:199` | fixed (B2, Q15) |
| 3 | Medium | Session `kind`/`run_id` self-declared by the client | `daemon.py:602` | fixed (B4) |
| 4 | Medium | Hook scanned the whole process table per call | `hook.py:33` | fixed (B5) |
| 5 | Medium | MCP moves bypassed the move lock (lost update reproduced) | `server.py:259` | fixed (B6) |
| 6 | Medium | Pickup decisions never pruned; requeued hand-offs kept stale decisions | `routes_runs.py:408` | fixed (B7) |
| 7 | Medium | Auto-headless while a non-channel session is live | `routes_runs.py:406` | fixed (B8, owner OK) |
| 8 | Low | Any directory registrable as a project | `daemon.py:593` | fixed (B9) |
| 9 | Low | Process helpers duplicated 3× | `routes_runs.py:77` | fixed (B10) |

## 3. Security review (/security-review + localhost abuse checklist)
`/security-review`: **no HIGH or MEDIUM vulnerability** with a concrete attack path (markdown rendering, UI sinks,
daemon auth order, spec file API, git/runner argv, MCP server, Tauri token/navigation/IPC, hook).

Abuse checklist (harness A, throwaway daemon, temporary `KANBAN_HOME`): **39/39 passed**; regression test
`plugins/kanban/tests/test_abuse.py` (`fixtures/abuse_probe.py`).
| ID | Probe (summary) | Expected | Observed | Status |
|---|---|---|---|---|
| AB-01 | posted actor, self-declared session kind, off-list/10 kB stage, editor PUT changing `status`/`approved_hash` | server-derived values; 400/409; file unchanged | actor `human (board)`; kind `interactive`; 400; 409 | pass |
| AB-02 | skip Approval (architect→done/developer), approve-chat with unknown session | 409 / 403 | 409 `approval_required`; 403 | pass |
| AB-03 | no/wrong token, foreign Origin, rebinding Host, CORS preflight, GET on mutating route | 401/403/no ACAO/405 | as expected | pass |
| AB-04 | cross-project approve-chat, other project's file via this project, unknown project | 403 / 4xx / 404 | as expected | pass |
| AB-05 | `<script>`, `"><img onerror>`, `{{7*7}}`, `javascript:`/`data:` links, quote breakout in https link, U+202E | escaped text, no script/handler/unsafe href | escaped; only https href with `&quot;` inside the attribute | pass |
| AB-06 | `../`, symlink out of specs, `/etc/hosts`, encoded traversal, static traversal | 4xx | 403/404 | pass |
| AB-07 | body > 4 MB | refused, file unchanged | connection closed before reading (client sees reset, not a 413 body) | pass (Low note) |
| AB-08 | Claude's token on move/approve/PUT/runs start/stop; UI token on client route | 403 | 403 | pass |
| AB-09 | login, rate limit, session cookies | — | N/A: no user accounts; bearer tokens per install | N/A |
| AB-10 | tokens in API responses, `daemon.json`, log, DB; home permissions | none; 0700/0600 | none; 0700/0600 | pass |
| AB-11 | headers, JSON type, malformed body | no-store, nosniff, DENY, CSP; 400 without trace | as expected | pass |

## 4. Independent verification (qa-verifier verdict)
- Pass 1: **NEEDS WORK** — 0 FAIL; blocking: `window.confirm()` unsupported in the app's WebView (Stop / discard edits),
  files outside PRD scope (the separately requested kit-process changes; not a defect), PRD-07 terminal item vs
  register; lows: "null" line in the approval dialog, project list refresh, stale register rows.
- Pass 2 (range `213d2de..cd37ffd`): **NEEDS WORK** — 9 PASS (incl. in-page Stop and discard modals, approval dialog
  without "null", live project list refresh, Q14 gate on entry, Q15 local approve, server-side session kind, ticket
  lock, pickup pruning, no auto-headless beside a session, project-root markers), 1 FAIL: the committed suite was red
  (B13). Desktop app items NOT VERIFIED by design (Demo). After the B13 fix: `Ran 130 tests in 116.398s` / `OK`.
- QA verdict: no open Critical or High finding; app-only requirements (FR-01, FR-16, NFR-08 smoke launch) move to the
  Demo with the kit owner.

## 5. Bugs
| # | Severity | Title | Requirement | Failing test (path::name) | Expected / actual | Status | Root cause · fix |
|---|---|---|---|---|---|---|---|
| B1 | High | Pre-v0.3 tickets stuck in execution | FR-19, Q14 | test_rules::test_transition_matrix, test_daemon::test_pre_v03_ticket_in_execution | developer→qa ok / "approval required" | closed | gate only on entry; "approved before v0.3" badge |
| B2 | High | No approval in local mode | FR-20, Q15 | test_mcp_channel::LocalModeTests::test_approve_local_mode_writes_markdown | markdown record / refused | closed | km.approve in local mode |
| B3 | High | Stop / discard edits no-op in the app | FR-15, FR-17 | test_specs_api::test_no_native_confirm | in-page modal / `window.confirm` | closed | `kanban.confirm` modal; xterm linkHandler |
| B4 | Medium | Self-declared session kind | NFR-03 | test_runs_api::test_session_kind_derived_server_side | interactive / headless | closed | kind derived from the runner's pid + start time |
| B5 | Medium | Hook scans all processes | NFR-06 | test_hook::test_ancestors_walk_only_the_chain | ancestor walk / `ps -A` | closed | walk ≤ 4 ancestors |
| B6 | Medium | Lost update between board and MCP moves | FR-12 | test_kanban::test_concurrent_writers_never_lose_an_update | consistent / tick reverted | closed | `km.ticket_lock` (flock on the ticket folder) |
| B7 | Medium | Pickup decisions leak, requeue not re-evaluated | FR-10 | test_runs_api::PickupDecisionTests | pruned / grows | closed | `reconcile_pickup` each tick |
| B8 | Medium | Auto-headless beside a live session | FR-10 | test_runs_api::PickupTests::test_live_non_channel_session_gets_an_offer | offer / headless | closed | auto only when no live session |
| B9 | Low | Any folder registrable | NFR-03 | test_daemon::test_project_root_must_be_a_project | 400 / 200 | closed | marker check |
| B10 | Low | Duplicated process helpers | — | test_db::ProcessHelperTests | one helper | closed | `kdb.process_start_time/alive` |
| B11 | Low | "null" in approval dialog | FR-19 | test_specs_api::test_approval_dialog_never_appends_null | no "null" | closed | filter falsy parts |
| B13 | High | Suite red at `cd37ffd`: a comment in `terminal.js` mentioned the native dialog call and matched `test_no_native_confirm` | NFR-08 | test_specs_api::test_no_native_confirm | green / FAILED (failures=1) | closed | comment reworded (orchestrator's own edit after the last full run; the test was not changed) |
| B14 | Medium | The v0.2 "New ticket" form was missing from the v0.3 board (found in the UX review, 2026-09-29) | FR-24 (no regression) | test_daemon::BoardTests::test_new_ticket_from_the_board | 200 + Discovery folder / 404 | closed | UI-token route `POST /api/projects/{p}/tickets` (also in UI_ONLY) + header form; `Ran 131 tests … OK` |
| B12 | Low | Project list not refreshed | FR-04 | test_daemon::test_new_project_is_announced | event / none | closed | `project.registered` event |

Severity: **Critical** blocks core functionality, no workaround · **High** major function broken, workaround exists ·
**Medium** partly works, minor impact · **Low** cosmetic. Fixed one at a time; no fix edited the test that caught it.

## 6. Residual risks
| Risk | Owner | Status |
|---|---|---|
| Desktop app not driven live (window, terminal, Stop in app, menu) — no screen access for agents | kit owner | Demo (06) |
| Same-user agent could read token files or hand-edit approval fields (Q12) | kit owner | accepted; detection badge + guard rules |
| Channels are a research preview; Code-tab sessions receive no channel events (Q04) | kit owner | accepted; headless / Copy prompt |
| Oversized requests are refused by closing the connection (no 413 body) | dev | Low, accepted |
| `git status` could run filters from a repo's own `.git/config` (same as the developer running it) | dev | accepted |
| Channel delivery into a real Claude session and headless runs with the real `claude` not exercised by agents | kit owner | Demo / first real use |
