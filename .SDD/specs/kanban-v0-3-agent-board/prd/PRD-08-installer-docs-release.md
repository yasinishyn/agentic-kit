---
title: PRD-08 - Installer, guard rules, docs and v0.3.0
status: todo
updated: 2026-09-28
---

# PRD-08: Installer, guard rules, docs and v0.3.0

| Field | Value |
|---|---|
| Requirements covered | KB3-FR-23; KB3-NFR-08; guard rules from architecture §8 |
| ADRs | ADR-001, ADR-007 |
| Depends on | PRD-01 … PRD-07 |
| Parallelisable | No — wave 5 |

## Goal
Ship v0.3: explicit installer component that builds the app locally, guard rules protecting the UI token and approval endpoints, permission prompt for `kanban_approve`, docs, version bump.

## Acceptance criteria
- [ ] `install.py --only kanban-app` only (not in defaults, `--all`, the manifest or `--update`): checks `cargo`, `cargo tauri --version`, Xcode CLT; prints install commands for anything missing; builds and copies `Kanban.app` to `~/Applications`; `--dry-run` prints commands; dispatch works for the hyphenated name
- [ ] Guard (`.claude/hooks/guard_bash.py`): deny reading `…/Kanban/ui.token` and HTTP requests from Bash to `127.0.0.1`/`localhost` paths matching `/api/.*(approve|move|file|runs)`; tests added
- [ ] Installer adds `mcp__plugin_kanban_kanban__kanban_approve` to `permissions.ask` and `Read(~/Library/Application Support/Kanban/ui.token)` to `permissions.deny` in the project settings it manages (the Bash guard does not cover the Read tool)
- [ ] `plugin.json` 0.3.0; marketplace entry updated
- [ ] `plugins/kanban/README.md`: app, daemon, tokens, hand-off (channels flag, Code-tab limitation, headless fallback), live indicator, approval rules, settings table, hooks
- [ ] Plugin README notes that deleting the DB drops board approval records, so tickets in execution need re-approval
- [ ] Root `README.md`: components, update section, board approval counts as SDD approval

## Owned files (only these may change)
- `install.py`
- `tests/test_install.py`
- `.claude/hooks/guard_bash.py`
- `.claude/hooks/test_guard_bash.sh`
- `README.md`
- `plugins/kanban/README.md`
- `plugins/kanban/.claude-plugin/plugin.json` (version)
- `.claude-plugin/marketplace.json`
- `LICENSES/NOTICE.md`

## Forbidden files
- Other PRDs' owned files; `.SDD/templates/**`; any SQLite migration outside `kanban_db.py` (PRD-02)

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_kanban_app_dry_run` | prints cargo tauri build + copy commands |
| `test_kanban_app_not_in_all_or_update` | --all and --update never build the app |
| `test_kanban_app_missing_prereqs` | missing cargo → hints, non-zero exit |
| `guard: ui token read / approve curl denied` | test_guard_bash.sh cases |

Review focus: the installer never downloads or runs remote scripts; it prints commands for the user.

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py' -v
python3 tests/test_install.py
claude plugin validate plugins/kanban && claude plugin validate .
```
```bash
bash .claude/hooks/test_guard_bash.sh
```

## Deliverable
The owned files changed and staged (`git add -- <owned>`), plus a short report: files changed, tests added/passing
(verbatim counts), the suggested commit message `PRD-08: <summary> (KB3-FR-..)`, open issues.
