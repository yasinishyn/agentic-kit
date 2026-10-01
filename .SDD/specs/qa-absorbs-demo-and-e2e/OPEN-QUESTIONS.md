# QA absorbs Demo and E2E — Open questions

Register only: one row per question. Record each decision in its row and in the ADR/PRD it changes, as
"Decided by <role>, <date>".

## Scope
| # | Question | Owner | Status | Decision / next step |
|---|---|---|---|---|
| Q01 | Stage shape: fold Demo and E2E into QA, or keep a separate verification stage after QA? | kit owner | Decided | Decided by kit owner, 2026-10-01: Discovery → Architect → Approval → Developer → QA → Done. QA = code and security review, an agent-driven browser run of the feature looking for UI defects, and committed E2E tests for the flow |
| Q02 | Where does the developer accept the work? | kit owner | Decided | Decided by kit owner, 2026-10-01: at the end of QA the agent presents the report with the browser-run evidence; the developer's "accept" is recorded like an approval |
| Q03 | What does Done mean? | kit owner | Decided | Decided by kit owner, 2026-10-01, revised the same day by Q07: Done means the ADLC stages are finished (QA, including the E2E tests, passed and accepted). Committing and pushing stay the developer's steps in the hand-off (agents never push, CLAUDE.md hard rule 1) and are not part of Done |

## Tooling
| # | Question | Owner | Status | Decision / next step |
|---|---|---|---|---|
| Q04 | Which E2E tool? | kit owner | Decided | Decided by kit owner, 2026-10-01: the kit stays tool-agnostic and uses the project's own E2E command (CLAUDE.md); for the kanban plugin itself, Playwright for Python as a dev-only dependency (never shipped; the tests skip when it is not installed) |
| Q05 | Which browser does the agent drive in QA? | kit owner | Decided | Decided by kit owner, 2026-10-01: the built-in browser pane (isolated, localhost) by default; Claude in Chrome when the developer asks for it and the project allows it (see Q09) |

## Compatibility and access
| # | Question | Owner | Status | Decision / next step |
|---|---|---|---|---|
| Q06 | Tickets whose README says `status: demo` or `status: e2e`: only read them as QA, or also rewrite this repo's existing tickets to `qa`? | kit owner | Decided | Decided by kit owner, 2026-10-01: the plugin reads `demo`/`e2e` as QA everywhere (README, stored hand-offs and runs); this repo's two v0.3 tickets are rewritten to `status: qa` |
| Q07 | What does a move to Done require? | kit owner | Decided | Decided by kit owner, 2026-10-01: when QA (including E2E) is finished, the agent moves the ticket to Done; there is no push check (amends Q03) |
| Q08 | On `install.py --update`, what happens to the obsolete `demo-report.md` / `e2e-report.md` templates in installed projects? | kit owner | Decided | Decided by kit owner, 2026-10-01: keep them (the installer never deletes) and print a note that they are no longer used |
| Q09 | The kit's `.claude/settings.json` denies Claude in Chrome (`mcp__claude-in-chrome`); Q05 wants it on request. Keep the deny, or change it (a setting only the developer edits)? | kit owner | Decided | Decided by kit owner, 2026-10-01: keep the deny; QA in this repo uses the built-in browser and Playwright; the docs say how a project enables Chrome if it wants it |
