---
title: PRD-07 - Desktop app shell (Tauri 2) and embedded terminal
status: done
updated: 2026-09-28
---

# PRD-07: Desktop app shell (Tauri 2) and embedded terminal

| Field | Value |
|---|---|
| Requirements covered | KB3-FR-01, 16; KB3-NFR-02, 03 |
| ADRs | ADR-001, ADR-007, ADR-008 |
| Depends on | PRD-02 |
| Parallelisable | Yes — wave 3, with PRD-03 and PRD-05; needs Rust + tauri-cli |

> **Unblocked 2026-09-28:** rustc 1.98.1, cargo 1.98.1, tauri-cli 2.12.0 installed by the owner; downloads of crates (crates.io) and @xterm/xterm 5.x (jsdelivr) approved by the owner in chat.

> Live terminal check in the running app (IPC → PTY → xterm, Shift+Esc, menu items) was not possible in the agent environment (screen capture / accessibility denied); carried to Demo. PTY layer covered by `cargo test` (15/15).

## Goal
A macOS Tauri 2 app that ensure-starts the daemon, shows the board in a native window with the UI token held in memory, and provides a PTY terminal per project.

## Acceptance criteria
- [x] `python3` resolved via login shell then `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`; error screen if missing or < 3.9
- [x] Runs bundled `daemon.py --ensure`; window loads the daemon origin; other navigations open in the default browser
- [x] UI token read by Rust and injected by an initialization script on the daemon origin only; never in URLs or logs
- [x] Capability `remote.urls` = exact daemon origin; terminal commands allowed on the main window only; `term_open(project_id, preset)` resolves the path through the daemon registry; presets shell and `claude --dangerously-load-development-channels plugin:kanban@agentic-kit`
- [ ] `ui/modules/terminal.js` (xterm.js) hidden in browser mode; keyboard focus can leave the terminal (documented shortcut)
- [x] Menu: Reload, Open in browser (`--open`), Stop board service (disabled when `GET /api/runs?live=1` reports runs; enabled if the endpoint is absent), Quit
- [x] `plugins/kanban/app/.gitignore` ignores `src-tauri/target/`; xterm.js vendored with its MIT licence (download only with the owner's permission)
- [x] `cargo test` green; `cargo tauri build --bundles app` produces `Kanban.app` · [ ] smoke launch by the user: Demo (agent smoke launch reached the daemon only)

## Owned files (only these may change)
- `plugins/kanban/app/**` (new, incl. `app/.gitignore`)
- `plugins/kanban/ui/modules/terminal.js` (new)
- `plugins/kanban/ui/vendor/xterm/**` (vendored)
- `LICENSES/xterm-LICENSE` (new)

## Forbidden files
- Other PRDs' owned files; `.SDD/templates/**`; any SQLite migration outside `kanban_db.py` (PRD-02)

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `nav_guard_blocks_foreign_origin` | https://example.com → opened externally, not in the window |
| `python_resolution_order` | fake PATHs → expected interpreter |
| `token_injection_origin_only` | script only for the daemon origin; URL has no token |
| `capability_scope` | terminal commands denied for a non-main window |
| `term_roundtrip` | `echo hi` → output event contains hi |

Review focus: navigation lock, IPC capability scope, project id resolution.

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py' -v
python3 tests/test_install.py
claude plugin validate plugins/kanban && claude plugin validate .
```
```bash
(cd plugins/kanban/app/src-tauri && cargo test) && (cd plugins/kanban/app && cargo tauri build --bundles app)
```

## Deliverable
The owned files changed and staged (`git add -- <owned>`), plus a short report: files changed, tests added/passing
(verbatim counts), the suggested commit message `PRD-07: <summary> (KB3-FR-..)`, open issues.
