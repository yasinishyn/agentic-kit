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
<1–2 sentences: the deliverable. What, not how; the why is in the discovery.>

## Steps
1. <step, in dependency order, independently checkable> — `path` (create / modify)

## Interfaces
| Input / output | Validation or consumer | Error states |
|---|---|---|

## Acceptance criteria
- [ ] <testable; references the step or FR id; non-functional ones carry a measurable threshold>

## Edge cases
| Case | Handling | Priority (must / should / could) |
|---|---|---|

## Out of scope
- <explicit; name the later PRD when known>

## Owned files (only these may change)
- `<src path>/...`

## Forbidden files
- `migrations/` (unless listed), shared registers, other PRDs' files

## Tests to write first (TDD)
| Test | Asserts |
|---|---|

## Gates (run in <your local environment>)
```bash
<your test command>
```

## Deliverable
The owned files changed, left staged/uncommitted for the developer (or committed as the developer on the stream branch,
if the project opted in to agent commits), plus a short report: files changed, tests added/passing (verbatim counts),
the suggested commit message `PRD-NN: <summary> (<SLUG>-FR-..)`, open issues. A PRD that cannot proceed is set to
`status: blocked` with the blocker type and one line (see `stages.md`, Developer).
