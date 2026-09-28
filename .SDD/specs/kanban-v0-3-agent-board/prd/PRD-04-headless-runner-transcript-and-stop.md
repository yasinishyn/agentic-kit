---
title: PRD-04 - Headless runner, transcript and Stop
status: done
updated: 2026-09-28
---

# PRD-04: Headless runner, transcript and Stop

| Field | Value |
|---|---|
| Requirements covered | KB3-FR-10, 11, 15; KB3-NFR-05 |
| ADRs | ADR-004 |
| Depends on | PRD-03 |
| Parallelisable | No — wave 4 |

## Goal
Run Claude headless when no session picks a hand-off up, stream its transcript to the board, keep the session id per ticket, surface permission denials, and allow Stop.

## Acceptance criteria
- [x] argv exactly `claude -p <headless_prompt> --output-format stream-json --verbose --permission-mode acceptEdits` (+ `--resume <id>` when stored); never `--dangerously-skip-permissions`; cwd = project root
- [x] Child env from the allow-list in architecture §5 step 7 (`CLAUDE_PROJECT_DIR=<root>`, `KANBAN_RUN_ID`, `KANBAN_HANDOFF_ID`; no inherited `CLAUDECODE` or other `CLAUDE_*`); `claude` resolved via login shell then known paths, clear failure if missing
- [x] stream-json → `run_events`; `system/init.session_id` → `ticket_sessions`; resume failure → one retry without `--resume`; `result.permission_denials` → events + card badge
- [x] Exit 0 → succeeded, else failed; Stop → SIGTERM, 5 s, SIGKILL on the process group → cancelled; `on_startup` marks orphaned runs failed; requeue of coalesced hand-offs on finish
- [x] `KANBAN_MAX_RUNS` (4) concurrent; one live run per ticket
- [x] `ui/modules/transcript.js` renders events with `textContent` only, paged by `afterSeq`, live via the event stream; Stop button (UI token); 'Run headless' action wired in `runs.js`; transcripts retained 30 days

## Owned files (only these may change)
- `plugins/kanban/scripts/runner.py` (new)
- `plugins/kanban/scripts/routes_runs.py`
- `plugins/kanban/ui/modules/runs.js`, `plugins/kanban/ui/modules/runs.css`
- `plugins/kanban/ui/modules/transcript.js` (new)
- `plugins/kanban/tests/test_runner.py`, `plugins/kanban/tests/fixtures/fake_claude.py` (new)

## Forbidden files
- Other PRDs' owned files; `.SDD/templates/**`; any SQLite migration outside `kanban_db.py` (PRD-02)

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_argv_exact` | literal argv incl. acceptEdits; no skip-permissions |
| `test_child_env_allowlist` | fake claude asserts CLAUDE_PROJECT_DIR=root, KANBAN_RUN_ID set, CLAUDECODE absent |
| `test_stream_json_to_events_and_session` | ordered events; session id stored |
| `test_resume_fallback` | fails with --resume → retried without |
| `test_permission_denials_badge` | fixture result with denials → events + run flag |
| `test_stop_kills_group` | grandchild gone; cancelled |
| `test_orphans_on_restart` | running → failed |
| `test_transcript_escapes_html` | `<img onerror>` fixture rendered as text |

Review focus: process-group kill, env allow-list, template-only prompts, transcript file modes 0600.

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py' -v
python3 tests/test_install.py
claude plugin validate plugins/kanban && claude plugin validate .
```

## Deliverable
The owned files changed and staged (`git add -- <owned>`), plus a short report: files changed, tests added/passing
(verbatim counts), the suggested commit message `PRD-04: <summary> (KB3-FR-..)`, open issues.
