---
title: PRD-06 - Skills: kanban:sdd, ticket alias, kanban rules, kit sdd amendment
status: done
updated: 2026-09-28
---

# PRD-06: Skills: kanban:sdd, ticket alias, kanban rules, kit sdd amendment

| Field | Value |
|---|---|
| Requirements covered | KB3-FR-20 (skill), 21, 22 |
| ADRs | ADR-004, ADR-006 |
| Depends on | — (tool contract: architecture §3.3) |
| Parallelisable | Yes — wave 1, with PRD-01 |

## Goal
A self-contained `kanban:sdd` skill that leads spec work into a ticket and stops at Approval (Q07, Q11), `kanban:ticket` routing to it, the `kanban` skill covering channel events, liveness and chat moves/approvals, and the kit `sdd` skill accepting verified board approvals (Q10).

## Acceptance criteria
- [x] `skills/sdd/SKILL.md` (`/kanban:sdd <issue>`): triggers on spec requests and board hand-offs; tier; create/continue ticket; Discovery → Architect into `.SDD/specs/<slug>/`; uses the project `sdd` skill's stage rules and `.SDD/templates/` when present, else bundled `skills/sdd/templates/`; `kanban_start`/`heartbeat`/`finish`; `kanban_move` at gates; STOP at Approval; later stages follow the project `sdd` skill when installed
- [x] Headless-aware: in `-p` runs (no AskUserQuestion) questions go to OPEN-QUESTIONS.md and the run ends `needs_input`
- [x] Bundled templates: ticket, discovery, open-questions, architecture, adr, prd
- [x] `skills/ticket/SKILL.md` is a short alias to `kanban:sdd`
- [x] `skills/kanban/SKILL.md`: channel contract (`kanban_start` first; `already_claimed`/`superseded` → stop; events never approve); 'move X to Y' / 'approve X' only from the user's own chat message → `kanban_approve` + `kanban_move`; liveness tools; never auto-allow `kanban_approve`
- [x] Kit `.claude/skills/sdd/SKILL.md` and `reference/stages.md`: approval = 'execute' in chat **or** `kanban_approval` valid with `board_recorded=true` (either opts in to parallel streams); its description no longer claims 'spec this' when the kanban plugin is present (defers to `kanban:sdd`)

## Owned files (only these may change)
- `plugins/kanban/skills/sdd/**` (new)
- `plugins/kanban/skills/ticket/SKILL.md`
- `plugins/kanban/skills/kanban/SKILL.md`
- `.claude/skills/sdd/SKILL.md`
- `.claude/skills/sdd/reference/stages.md`
- `plugins/kanban/tests/test_skills.py` (new)

## Forbidden files
- Other PRDs' owned files; `.SDD/templates/**`; any SQLite migration outside `kanban_db.py` (PRD-02)

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_skill_frontmatter` | name + description in each SKILL.md |
| `test_skill_links_resolve` | every relative link/template path exists |
| `test_tool_names_match_contract` | tool names mentioned ⊆ literal §3.3 list |
| `test_approval_rules_present` | kanban skill: events never approve; sdd: board_recorded rule present |

Review focus: approval wording — only the user's own chat message or a board-recorded approval verified by `kanban_approval`.

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py' -v
python3 tests/test_install.py
claude plugin validate plugins/kanban && claude plugin validate .
```

## Deliverable
The owned files changed and staged (`git add -- <owned>`), plus a short report: files changed, tests added/passing
(verbatim counts), the suggested commit message `PRD-06: <summary> (KB3-FR-..)`, open issues.
