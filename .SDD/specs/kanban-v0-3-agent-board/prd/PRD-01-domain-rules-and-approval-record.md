---
title: PRD-01 - Domain rules, transition guard and approval record
status: done
updated: 2026-09-28
---

# PRD-01: Domain rules, transition guard and approval record

| Field | Value |
|---|---|
| Requirements covered | KB3-FR-05, KB3-FR-12, KB3-FR-14 (state logic), KB3-FR-19 (record + guard); KB3-NFR-04 |
| ADRs | ADR-002, ADR-004, ADR-005, ADR-006 |
| Depends on | — |
| Parallelisable | Yes — wave 1, with PRD-06 |

## Goal
Pure decision functions every later slice uses (architecture §2), and a markdown store in which no caller can put a ticket into Developer or later without a recorded approval.

## Acceptance criteria
- [x] `transition_allowed(src, dst, approval)` implements §2 / Q09 literally: entering developer/qa/demo/e2e/done from discovery/architect/approval needs a valid approval; moves inside that set need an approval record; backward moves always allowed
- [x] `move_ticket` is the only README stage writer and calls `transition_allowed`; errors say `approval required` or `spec changed since approval`
- [x] `set_status` refuses a ticket README (`use kanban_move`); status and stage enums validated server-side for every entry point
- [x] `handoff_kind(src, dst, actor)` returns the §2 table (only actor `human (board)` produces kinds; `start` forward into WORKING; `rework` backward into WORKING; none for approval/done/same stage)
- [x] `spec_hash` covers 01-*, 02-*, 03-*, adr/*, prd/*, OPEN-QUESTIONS.md with the §2 normalisation: equal after `approve()`, equal after ticking any checkbox or changing `status`/`updated`, different after any text edit
- [x] `approve(root, ticket, actor)` writes `approved_by/at/hash` and the approval line under the 03-architecture.md header (README only when no 03-*); re-approval replaces, never duplicates
- [x] `view_state` → `stale` past TTL for running/waiting; terminal states unchanged
- [x] `channel_event(handoff)` and `headless_prompt(handoff)` contain slug and stage names only; slug regex enforced; the developer hand-off text tells Claude to verify with `kanban_approval`
- [x] Existing `test_kanban.py` cases that skip Approval (lines 64, 195) are updated to approve first; all other existing tests unchanged and green

## Owned files (only these may change)
- `plugins/kanban/scripts/kanban_rules.py` (new)
- `plugins/kanban/scripts/kanban_md.py`
- `plugins/kanban/tests/test_rules.py` (new)
- `plugins/kanban/tests/test_kanban.py`

## Forbidden files
- Other PRDs' owned files; `.SDD/templates/**`; any SQLite migration outside `kanban_db.py` (PRD-02)

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_transition_matrix` | literal table: approval→qa, architect→developer, discovery→qa, approval→done refused without approval; developer→qa ok with a record; qa→architect ok |
| `test_set_status_refuses_readme` | `set_status(README, 'developer')` raises; PRD file statuses still work |
| `test_handoff_kind_table` | every row of §2 with literal expected kinds |
| `test_spec_hash_normalisation` | equal after approve and after ticking a PRD box; differs after a text edit; 02-* included |
| `test_approval_line_and_readme_only` | exact line text; replaced on re-approve; README-only when no 03-* |
| `test_view_state_stale` | 901 s since heartbeat, ttl 900 → stale; succeeded stays succeeded |
| `test_templates_never_include_title` | malicious title absent; bad slug rejected |

Review focus: every path that writes a README `status:` goes through `transition_allowed`.

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py' -v
python3 tests/test_install.py
claude plugin validate plugins/kanban && claude plugin validate .
```

## Deliverable
The owned files changed and staged (`git add -- <owned>`), plus a short report: files changed, tests added/passing
(verbatim counts), the suggested commit message `PRD-01: <summary> (KB3-FR-..)`, open issues.
