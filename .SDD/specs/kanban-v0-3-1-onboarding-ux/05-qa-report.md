# Kanban v0.3.1: onboarding and UX — QA report

| Run | Date | Branch · base commit · staged/uncommitted |
|---|---|---|
| QA 1 | 2026-09-30 | main · reviewed f600ff9 → working tree (HEAD 43722f9 + staged Q18 licences + QA fixes, uncommitted) |

## 1. Test results (verbatim counts, vs the baseline)
Baseline at f600ff9: root 11, plugin 131, cargo 15.

| Suite | Command | Result |
|---|---|---|
| Root | `python3 -m unittest discover -s tests -p 'test_*.py'` | Ran 87 tests · OK (skipped=1) (baseline 11) |
| Guard | `bash .claude/hooks/test_guard_bash.sh` | passed=62 failed=0 |
| Cargo | `cargo test --locked` (src-tauri) | 22 passed; 0 failed (baseline 15) |
| Validate | `claude plugin validate plugins/kanban` · `claude plugin validate .` | ✔ Validation passed ×2 |
| Plugin ×5 | `python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py'` | Ran 193 tests · OK in 5/5 runs, 0 failures (baseline 131); after B10: Ran 194 · OK 3/3 |

The real `~/Library/Application Support/Kanban` was unchanged by the runs. `test_package_host_only` stays skipped
(needs `KANBAN_TEST_PACKAGE=1`, fetched runtimes and a full build).

## 2. Code review findings (/code-review)
Full range `git diff f600ff9`, high recall; each finding checked against the code before it was accepted.

| # | Severity | Finding | File:line | Status |
|---|---|---|---|---|
| CR1 | High | App update always deferred: the board daemon and every Claude session's MCP server run on the bundled Python, so `running_under` never clears; the note did not say how to stop the daemon | `install.py` `running_under`, swap loop | Fixed (B1) |
| CR2 | Medium | Unsigned `Kanban.dmg` holds the app from before the ad-hoc outer seal; only the zip was verified | `plugins/kanban/app/scripts/package-release.sh` DMG step | Fixed (B2) |
| CR3 | Medium | `fetch-python.sh --licences-only` not atomic; a failed copy leaves a hidden folder in the tree that would be bundled, or a tree with no `licences/` | `plugins/kanban/app/scripts/fetch-python.sh` | Fixed (B3) |
| CR4 | Medium | `package-release.sh` never checks the lock's licence files; a pre-Q18 runtime ships without them | `package-release.sh` pre-build check | Fixed (B4) |
| CR5 | Medium | Remove/re-add and board hints say `python3 …/daemon.py`; the plugin may run only on the bundled Python | `plugins/kanban/scripts/server.py` | Fixed (B5) |
| CR6 | Low | The "not a github.com repository" note echoes the repo URL, including a token in a clone's origin | `install.py` `download_kanban_app` | Fixed (B6) |
| CR7 | Low | `session.changed live=false` published when one of two streams of a reconnecting session closes | `plugins/kanban/scripts/daemon.py` `h_events` | Fixed (B7) |
| CR8 | Low | Onboarding `plugin_python` hint resolves PATH in the daemon (launchd PATH when the app started it), not the user's shell | `daemon.py` `plugin_python_hint` | Open → residual risk R1 |
| CR9 | Low | `kpython` run-checks the interpreter with an extra Python start on every hook call (2 s timeout) | `plugins/kanban/scripts/kpython` | Open → residual risk R2 |
| CR10 | Low | `find_python` always spawns a login shell (up to 8 s) even when the bundled interpreter is used | `app/src-tauri/src/shell.rs` | Open → residual risk R2 |

## 3. Security review (/security-review + localhost abuse checklist)
`/security-review` over the range: no HIGH or MEDIUM findings. Abuse checklist: the probes live in the suite
(`plugins/kanban/tests/test_abuse.py`, 59/59 rows) and pass; new routes are UI-token only (`test_new_routes_ui_only`),
no inline script, one documented `innerHTML` sink. CR6 (a credential in a printed URL) was found by code review and
fixed under the kit's "never print secrets" rule.

## 4. Independent verification (qa-verifier verdict)
Verdict **NEEDS WORK** — 14 PASS · 2 FAIL · 7 NOT VERIFIED. Blocking: drawer focus lost on Escape (NFR-02) and no
New-ticket control in the welcome's first-card step (FR-03); both fixed test-first (B8, B9). Low issues: files
outside PRD owned lists (root `.gitignore` `/dist/`, `test_hook.py` expected command — neither weakens anything);
Q18 not traced in PRD-01/07 (now amended in both); `REQUIREMENT-COVERAGE.csv` still `planned` (now updated).
NOT VERIFIED items go to Demo (native folder picker, Connect Claude terminal, Terminal tab) and E2E (CI release run,
signed/unsigned artefacts, real download, real bundled runtime).

## 5. Bugs
| # | Severity | Title | Requirement | Failing test (path::name) | Expected / actual | Status (open / fixed / closed) | Root cause · fix |
|---|---|---|---|---|---|---|---|
| B1 | High | App update deferred forever | FR-16, FR-17 | `tests/test_install.py::test_app_board_daemon_on_bundled_python_deferred_with_stop_command`, `::test_app_claude_sessions_on_bundled_python_do_not_block` | Only the app and the board daemon block the swap, with the exact stop command / every process under the bundle blocked | fixed | All bundle processes treated alike · `running_under` classifies app / daemon (by `daemon.py` in args) / other; the note names `sh <kpython> <daemon.py> --stop`; MCP servers and hooks never block (they run by path) and get a restart note. Never kills (ADR-003) |
| B2 | Medium | Unsigned DMG not sealed | FR-14, FR-18 | `tests/test_release_workflow.py::test_unsigned_dmg_holds_the_sealed_app` | DMG built from the sealed, verified app / Tauri DMG from before the seal | fixed | Tauri builds the DMG inside `run_build` · unsigned mode rebuilds it with `hdiutil` from the verified app (plus an Applications link) |
| B3 | Medium | `--licences-only` not atomic | NFR-01 | `tests/test_app_python_lock.py::test_licences_only_failure_leaves_tree_untouched` | Failure leaves the tree as it was / old licences deleted | fixed | Staged in the tree, rm before check · stage and check every arch in a temp dir (trap cleanup), then swap |
| B4 | Medium | Packaging without licence texts | NFR-01 | `tests/test_release_workflow.py::test_runtime_without_licences_exits_1` | exit 1 naming `--licences-only` / packaged | fixed | Pre-build check tested only `bin/python3` · every `licence_files` entry must exist in each runtime |
| B5 | Medium | Hints point at bare `python3` | FR-13 | `plugins/kanban/tests/test_kanban.py` board-URL test, `test_mcp_channel.py` removed-project test (tightened) | `sh …/kpython …/daemon.py` / `python3 …/daemon.py` | fixed | Pre-kpython strings · one `daemon_command()` helper |
| B6 | Low | Repo credentials printed | NFR-03 (hard rule 2) | `tests/test_install.py::test_app_repo_credentials_never_printed` | `***@` / token printed | fixed | Raw string in the note · `redact_url` |
| B7 | Low | Session shown disconnected while live | FR-04 | `plugins/kanban/tests/test_daemon.py::test_session_stays_live_while_another_stream_is_open` | live until the last stream closes / live=false on the first close | fixed (test fails without the fix, checked) | Unconditional publish in `finally` · publish only when no stream of the session remains |
| B8 | Medium | Drawer close loses focus | NFR-02 | `plugins/kanban/tests/test_ui_contract.py::test_drawer_close_returns_focus` | Focus back on the opener (or its re-rendered card) / page start | fixed (browser re-check → Demo) | Inherited from v0.3 · `openDrawer` remembers the opener; `closeDrawer` restores it (or the card found by `data-ticket`, else the board) only when focus was in the drawer |
| B9 | Medium | Welcome step 3 lacks New ticket | FR-03 | `plugins/kanban/tests/test_ui_contract.py::test_welcome_first_card_step_has_new_ticket` | New ticket button in step 3 / text only | fixed (browser re-check → Demo) | Dropped in PRD-05 · button opens the header popover |
| B10 | Critical | Daemon leaks one SQLite connection per request; after ~120 requests every call fails ("Load failed" in the app) | NFR-04 | `plugins/kanban/tests/test_daemon.py::test_requests_do_not_leak_db_connections` | kanban.db handles stay flat / 155 handles after 150 requests | fixed (found in the installed 0.3.0 app on 2026-09-30: 123 connections, 246 of 256 fds, 735 "unable to open database file") | `Store` kept every thread's connection in `_all`; each request runs on a new thread · a new connection closes those of ended threads |

Severity: **Critical** blocks core functionality, no workaround · **High** major function broken, workaround exists ·
**Medium** partly works, minor impact · **Low** cosmetic. Fixed one at a time; a fix never edits the test that caught
the bug.

## 6. Residual risks
- R1 (CR8): the welcome may say "the plugin needs Python" when the app runs from outside the Applications folders
  and python3 exists only on the user's shell PATH. Workaround: move Kanban.app to Applications. Follow-up ticket.
- R2 (CR9, CR10): extra process starts on hook calls and daemon start/stop; a latency cost, not a failure. Follow-up
  ticket (cache the run-check; resolve the login-shell fallback lazily).
- B8/B9 were verified by contract tests only: an in-browser check was refused by the session's permission
  classifier (token in the URL), so both are Demo checks.
- A running MCP server keeps the previous interpreter process after an app update and loads stdlib modules from the
  new tree until the session restarts (same Python minor version by the lock); the installer prints a restart note.
- A `term:exit` arriving before `term_open` resolves is dropped in `terminal.js` (low probability; noted by review).
- E2E-only items (CI release, signing, real download, real bundled runtime) remain unverified until the first
  `workflow_dispatch` dry run and a tag.
