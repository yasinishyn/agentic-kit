---
title: PRD-05 - Approval dialog, spec editor with diff, git panel
status: done
updated: 2026-09-28
---

# PRD-05: Approval dialog, spec editor with diff, git panel

| Field | Value |
|---|---|
| Requirements covered | KB3-FR-17, 18, 19 (UI + endpoints); KB3-NFR-02 |
| ADRs | ADR-006, ADR-007 |
| Depends on | PRD-01, PRD-02 |
| Parallelisable | Yes — wave 3, with PRD-03 and PRD-07 |

## Goal
Human approval on the board, in-app editing of ticket markdown with a diff since approval, and a read-only git panel.

## Acceptance criteria
- [x] `beforeMove`: any move that `transition_allowed` answers `approval_required`/`spec_changed` opens a dialog (files + hash12); 'Approve and execute' → `POST …/approve` (UI token) → move; Cancel keeps the card; focus trapped and returned
- [x] Approve uses `kanban_db.record_approval` via the PRD-02 endpoint (actor `human (board)`); `board_recorded` = latest approvals row has actor `human (board)`
- [x] Badges: 'spec changed since approval' (with diff link) and 'approval not recorded' (README `approved_*` without any matching `approvals` row, whatever the actor)
- [x] `GET/PUT …/file?path=` confined to `.SDD/specs/**.md` (symlinks resolved), PUT needs `If-Match` (409 on mismatch), refuses changes to `status` and `approved_*` frontmatter keys, atomic write
- [x] `GET …/diff/<slug>` unified diff since the snapshot
- [x] `GET …/git`: argv exactly `git --no-optional-locks -c core.fsmonitor= -C <root> status --porcelain=v1` and `git -C <root> rev-parse --abbrev-ref HEAD`; latest `handoff-note.md` git block shown read-only
- [x] Editor keyboard accessible (textarea, Cmd/Ctrl-S), labels, visible focus

## Owned files (only these may change)
- `plugins/kanban/scripts/routes_specs.py` (new)
- `plugins/kanban/ui/modules/specs.js`, `plugins/kanban/ui/modules/specs.css` (new)
- `plugins/kanban/tests/test_specs_api.py` (new)

## Forbidden files
- Other PRDs' owned files; `.SDD/templates/**`; any SQLite migration outside `kanban_db.py` (PRD-02)

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_file_traversal_refused` | ../, symlink out of specs, .py → 4xx |
| `test_put_if_match_and_protected_keys` | stale hash 409; status/approved_* change 409; content unchanged |
| `test_approve_snapshot_diff_and_badges` | edit after approve → diff line + changed badge; hand-edited approved_* → not-recorded badge |
| `test_git_argv_exact` | subprocess argv lists as specified |
| `test_approve_then_move` | approval→developer succeeds only after approve |

Review focus: path confinement incl. symlinks, protected keys, git argv.

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py' -v
python3 tests/test_install.py
claude plugin validate plugins/kanban && claude plugin validate .
```

## Deliverable
The owned files changed and staged (`git add -- <owned>`), plus a short report: files changed, tests added/passing
(verbatim counts), the suggested commit message `PRD-05: <summary> (KB3-FR-..)`, open issues.
