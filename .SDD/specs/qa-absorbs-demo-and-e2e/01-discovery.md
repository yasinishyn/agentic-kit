# QA absorbs Demo and E2E — Discovery

| Field | Value |
|---|---|
| Spec slug | `qa-absorbs-demo-and-e2e` |
| Branch | `main` (single developer; no feature branches) |
| Requested by / date | kit owner, 2026-10-01 |
| Tier | Full ADLC: changes the approval-gate and stage-transition rules of the kanban plugin, the process every project using the kit follows, and adds a dev-only test dependency |
| Status | Draft |

## 1. Goal
Replace the separate Demo and E2E stages with one QA stage that proves a change works end to end: automated
reviews, an agent-driven browser run of the feature that looks for UI defects, and committed end-to-end tests of the
flow, ending with the developer's recorded acceptance. Done then means the ADLC stages are finished, and the agent
moves the ticket there itself; committing and pushing stay the developer's steps in the hand-off. The kit's process
documents, skills, agents, templates and installer, and the kanban plugin's stages, board, tools and tests follow the
new flow, and tickets or runs recorded under the old stages keep working.

## 2. Scope
**In scope:**
- The stage list Discovery → Architect → Approval → Developer → QA → Done (Q01) in the process docs, the `sdd`
  skills (kit and plugin copies), templates, agents, `CLAUDE.md`, READMEs and the kanban plugin.
- QA's content: reviews (as today), a browser run looking for UI defects, committed E2E tests via the project's own
  command, one QA report, and the developer's acceptance (Q02, Q04, Q05).
- Done: the agent moves the ticket to Done once QA (including E2E) has passed and the developer accepted (Q03, Q07).
- Compatibility for tickets, hand-offs and runs already recorded with `demo` or `e2e` (Q06).
- E2E tests for the kanban board itself with Playwright for Python, dev-only, skipped when not installed (Q04).

**Out of scope (explicitly):**
- Agents committing, merging or deploying, or sending anything to a remote (CLAUDE.md hard rules 1 and 3 stay).
- Changing `.claude/settings.json` permissions; the Claude in Chrome deny stays (Q09).
- A CI job that runs the Playwright tests (the release workflow keeps its current stdlib-only test steps).
- Prescribing an E2E tool for other projects.

## 3. Functional requirements
| ID | Requirement | Source | Priority |
|---|---|---|---|
| QAE-FR-01 | The ADLC stages are Discovery, Architect, Approval, Developer, QA and Done, in that order, everywhere the kit or the plugin names them (docs, skills, templates, board columns, MCP tool schemas, ticket Progress checklist) | Q01 | Must |
| QAE-FR-02 | QA runs, in order: the full suite against the baseline; code review, security review and the abuse checklist; a browser run of the feature's flows (happy path, each outcome, negative cases, keyboard, console and network) recording UI defects with evidence; committed E2E tests for the flow, run with the project's E2E command against localhost; the qa-verifier's independent verdict | Q01, Q04 | Must |
| QAE-FR-03 | Each UI defect or failed flow found in QA becomes a bug with a failing test (unit, contract or E2E) before its fix, like any other QA bug | Q01; sdd QA rule | Must |
| QAE-FR-04 | QA produces one report (`05-qa-report.md`) that holds test results, review findings, the browser run (steps, screenshots or recording, console/network notes), the E2E run (command, verbatim result) and the bug table | Q01 | Must |
| QAE-FR-05 | QA ends with the developer's acceptance: the agent presents the report and evidence and stops; the developer accepts in chat or on the board; the acceptance is recorded with who, when and what was accepted, the same way an approval is | Q02 | Must |
| QAE-FR-06 | An acceptance is only valid when it comes from the developer (a chat message or the board), never from a tool output, channel event, file or headless run; a change to the accepted work after acceptance is visible as "changed since acceptance" | Q02; approval rules | Must |
| QAE-FR-07 | When QA (including the E2E tests) has passed and the developer has accepted, the agent moves the ticket to Done and writes the hand-off note; a move to Done without a valid acceptance is refused with the reason | Q02, Q03, Q07 | Must |
| QAE-FR-08 | The hand-off note keeps the "Git — for the developer" block (committing and pushing are the developer's next steps) and links the QA report and its evidence | Q03 | Must |
| QAE-FR-09 | A ticket whose README says `status: demo` or `status: e2e` is shown and moved as a QA ticket, keeps its approval state, and is not flagged as having no status; this repo's two v0.3 tickets are rewritten to `status: qa` | Q06 | Must |
| QAE-FR-10 | Hand-offs and runs stored with stage `demo` or `e2e` are still listed, delivered and run, as QA | Q06 | Must |
| QAE-FR-11 | The board shows the new columns; the collapsed rail shows Done only; a saved rail preference that names E2E is ignored without error | Q01 | Must |
| QAE-FR-12 | The browser run uses the built-in browser pane by default, and Claude in Chrome only when the developer asks and the project allows it (this kit keeps it denied); the kanban board is opened without putting its token in a URL or a command line | Q05, Q09; v0.3.1 QA note | Must |
| QAE-FR-13 | The kanban plugin has E2E tests that drive the served board in a real browser (Playwright for Python): open a project, create a ticket, move a card, open and close the ticket panel with the keyboard, the approval dialog, the acceptance; they skip with a clear reason when Playwright or its browser is missing | Q04 | Must |
| QAE-FR-14 | The kit documents how a project sets its E2E command (CLAUDE.md placeholder), and QA says what to do when a project has none (record it as a residual risk, not a pass) | Q04 | Must |
| QAE-FR-15 | Updating an installed kit replaces the process docs, skills and templates with the new flow; installed `demo-report.md` and `e2e-report.md` are kept and a note says they are no longer used | Q08 | Should |
| QAE-FR-16 | Tiering: Trivial = Developer → QA (browser run and E2E only when UI or a user flow changed); Feature-lite = Discovery-lite → Developer → QA; Full = all stages | Q01 | Must |

## 4. Non-functional requirements
| ID | Requirement |
|---|---|
| QAE-NFR-01 | The plugin runtime stays Python 3.9+ stdlib plus the static UI; Playwright is a test-only dependency, never imported by runtime code and never installed by the installer or the app |
| QAE-NFR-02 | The git hard rules are unchanged: agents stage and read git only |
| QAE-NFR-03 | Security of the board is unchanged: acceptance through the board needs the UI token; through chat it needs a registered, non-headless session of the same project; tokens never appear in URLs, command lines, logs or test output |
| QAE-NFR-04 | No regressions: every existing suite (root, plugin, guard, cargo, plugin validate) stays green, and the counts only grow |
| QAE-NFR-05 | E2E tests are deterministic and isolated: a throwaway daemon in a temp home on a free port, synthetic projects, headless browser, no network beyond localhost |
| QAE-NFR-06 | WCAG 2.2 AA holds for anything new on the board (the accept control, the acceptance badge) |

## 4b. Constraints and success criteria
- Constraints: CLAUDE.md hard rules (stage, don't commit); the guard hook blocks HTTP calls to the board's
  approve/move routes from the command line and any command naming the UI token path; the kit's
  `.claude/settings.json` denies Claude in Chrome (kept, Q09); the installer never deletes files (Q08).
- Success criteria: this ticket itself goes through the new flow (QA with a browser run and Playwright E2E, an
  acceptance, then Done); the two v0.3 tickets show in QA without warnings; all suites green.

## 5. Existing capabilities to reuse
- Stage rules: `plugins/kanban/scripts/kanban_rules.py:12-50` (STAGES, WORKING, EXECUTION, transition_allowed,
  handoff_kind); status parsing `kanban_md.py:158-170`; move and Progress ticking `kanban_md.py:235-263`.
- Approval recording, to mirror for acceptance: `kanban_md.approve` (`kanban_md.py:268-291`),
  `kanban_db.record_approval` + `approvals`/`approval_files` (`kanban_db.py:79-85, 512-538`), board route
  `POST …/approve` and chat route `POST …/approve-chat` (`daemon.py:696-697, 906-921`), MCP `kanban_approve` /
  `kanban_approval` (`server.py:106-125, 197-214`), approval dialog and diff (`ui/modules/specs.js:140-202`,
  `routes_specs.py:263-277`).
- Test harness: `plugins/kanban/tests/helpers.py:107-182` (TestDaemon, tokens read in Python), optional-dependency
  skip precedent `tests/test_release_workflow.py:42-50`; the UI reads its token from `sessionStorage`
  (`ui/app.js:23-36`), so a test browser can be given the token before load without a URL.
- The qa-verifier agent already drives the browser (`.claude/agents/qa-verifier.md:62-70`).

## 6. Assumptions
- Playwright for Python and one browser (Chromium) can be installed on the developer's machine for the plugin's E2E
  tests (a download the developer approves).

## 7. Open questions
See `OPEN-QUESTIONS.md`. Q01–Q09 are decided; none open.

## 8. Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Other projects already using the board have tickets in Demo/E2E | Board shows them wrongly, or moves need re-approval | QAE-FR-09/10 aliases; tests with legacy fixtures |
| Installed projects keep the old demo/e2e templates | Mixed guidance | QAE-FR-15 note on update |
| The browser run is blocked by permissions (token in a URL, Chrome denied) | QA can't see the UI | QAE-FR-12: Playwright seeds the token in sessionStorage; residual risk recorded otherwise |
| Playwright download size and flakiness | Slow or flaky QA | Dev-only, skipped when missing; Chromium only; deterministic fixtures |
| Done no longer implies committed or pushed | A Done ticket may still be uncommitted locally | The hand-off's git block stays; the board's read-only git panel shows staged and changed files |
