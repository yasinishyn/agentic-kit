# Kanban v0.3.1 — Open questions

Register only: one row per question. Record each decision in its row and in the ADR/PRD it changes, as
"Decided by <role>, <date>".

## Scope
| # | Question | Owner | Status | Decision / next step |
|---|---|---|---|---|
| Q01 | The "New ticket" form of v0.2 is missing in v0.3 (regression). Fix it inside v0.3 before its hand-off, or in v0.3.1? | kit owner | Decided | Decided by kit owner, 2026-09-29: fixed in v0.3 as QA bug B14 before its hand-off; KB31-FR-03 then only covers its place in the new header |
| Q02 | Visual direction for the design pass: keep the current neutral look and tidy it, or a distinct identity (brand colour, type scale)? | kit owner | Decided | Decided by kit owner, 2026-09-29: tidy the neutral look with design tokens; no brand identity |
| Q03 | Board width: collapse Done/E2E by default, or fit all 8 columns by narrowing them? | kit owner | Decided | Decided by kit owner, 2026-09-29: Done and E2E collapsed to thin columns with counts, expandable |
| Q04 | Should "Add project" also offer to scaffold `.SDD/specs` (and the kit) in a folder that has only `.git`? | kit owner | Decided | Decided by kit owner, 2026-09-29: offer to create `.SDD/specs` only; the kit stays `install.sh` |

## UI
| # | Question | Owner | Status | Decision / next step |
|---|---|---|---|---|

## Testing
| # | Question | Owner | Status | Decision / next step |
|---|---|---|---|---|
