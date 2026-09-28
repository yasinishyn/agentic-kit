---
title: PRD-NN - <slice title>
status: todo   # todo | doing | blocked | done
updated: <YYYY-MM-DD>
---

# PRD-NN: <slice title>

| Field | Value |
|---|---|
| Requirements covered | <SLUG>-FR-.. |
| ADRs | ADR-.. |
| Wave · Requires | <n> · PRD-.. |
| Parallelisable | Yes / No |

## Goal
<1–2 sentences: the deliverable. What, not how.>

## Steps
1. <in dependency order, independently checkable> — `path` (create / modify)

## Acceptance criteria
- [ ] <testable; non-functional ones with a measurable threshold>

## Edge cases
| Case | Handling | Priority (must / should / could) |
|---|---|---|

## Out of scope
- …

## Owned files (only these may change)
- `<src path>/...`

## Forbidden files
- other PRDs' owned files, shared registers, migrations (unless listed)

## Tests to write first (TDD)
| Test | Asserts |
|---|---|

## Gates
```bash
<your test command>
```

## Deliverable
The owned files changed and staged for the developer's commit, plus a short report: files changed, tests
added/passing (verbatim counts), the suggested commit message `PRD-NN: <summary> (<SLUG>-FR-..)`, open issues.
