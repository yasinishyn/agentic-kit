---
title: PRD-05 - UI design pass, onboarding and plug-in API (web)
status: done
updated: 2026-09-29
---

# PRD-05: UI design pass, onboarding and plug-in API (web)

| Field | Value |
|---|---|
| Requirements covered | KB31-FR-01, 02 (web), 03, 04, 05 (browser path + panel), 06, 07, 08, 09, 10; KB31-NFR-02, 03 |
| ADRs | ADR-005, ADR-006, ADR-004 (labels) |
| Wave · Requires | 2 · PRD-03 (routes, events, onboarding state) |
| Parallelisable | Yes — wave 2 (with PRD-04) |

Paths are relative to `plugins/kanban/`.

## Goal
A legible, token-based board with a guided first run, Add/Remove project with a preview, New ticket in the header, a
live connection panel, a tabbed ticket panel and the extended plug-in API that PRD-06 builds on (architecture §4.1).

## Steps
1. Tests first (see table) — `tests/test_ui_contract.py` (create)
2. Tokens: spacing 4/8/12/16/24, type 12/13/15/18, radius, neutral palette, accent, status colours in `:root` with a
   dark variant (`prefers-color-scheme`); classes `panel`, `muted`, `term-bar` for modules; every colour literal lives
   in the token blocks — `ui/board.css` (modify)
3. Plug-in API (`app.js:324-338`): `addDrawerPanel(id, title, render, {tab} = {})` with `tab` ∈
   `overview|spec|runs|terminal|git` (missing/unknown → `overview`); `addProjectPanel(id, title, render)` rendering into
   the project panel area on project switch, board reload and immediately when added; `window.kanban` stays extensible
   (not frozen) so a module can set `connectClaude` — `ui/app.js` (modify)
4. Header: title · project switcher · Add project · New ticket (B14 form, restyled) · connection chip ("n sessions ·
   channels on/off") · help menu (the old hint text moves here; "Show welcome" item) — `ui/index.html`, `ui/app.js`
5. Welcome: no project → step 1 only active; selected project → three steps from `GET /api/projects/{p}/onboarding`,
   refreshed on `session.changed`, `board.changed` and `project.registered`; dismiss → `POST …/onboarding
   {dismissed: true}`; `plugin_python: missing` shows the fix under Connect Claude (move Kanban.app to Applications,
   or install python3) — `ui/app.js`, `ui/board.css`
6. Add project dialog: app → `window.__TAURI__.core.invoke("pick_folder")` when available (PRD-06) else a path field;
   then `POST /api/projects/add {path, dry_run: true}` → preview (resolved path, markers, "create `.SDD/specs`"
   checkbox when `has_specs` is false, warning when `is_home`, note when `already_registered`) → Confirm sends the real
   call; Remove project (help menu or switcher) → confirm → `DELETE /api/projects/{p}`; `project.removed` switches to
   another project or the welcome — `ui/app.js`
7. Connection panel (project panel "connection", registered by core via `addProjectPanel`): sessions from
   `GET /api/projects/{p}/sessions` with labels (CLI + channels, CLI, CLI print, Code tab, headless, unknown) and last
   seen; refetched on `session.changed` and on stream reconnect; Connect Claude → `window.kanban.connectClaude(p)` when
   it is a function, else the command `claude --dangerously-load-development-channels plugin:kanban@agentic-kit` with
   Copy; Code-tab explanation — `ui/app.js`
8. Board: Done and E2E as 44 px rails with name + count, expand/collapse on click/Enter/Space, state in `localStorage`
   (try/catch); six working columns share the width — `ui/board.js`, `ui/board.css`
9. Cards: title, badges, live indicator, sub-task progress `done/total`; ← / → and card actions in a "⋯" menu
   (menu button pattern: Enter/Space/ArrowDown open, arrows move, Esc closes and returns focus); drag unchanged —
   `ui/board.js`
10. Ticket panel tabs (tablist pattern: arrow keys, Home/End, `aria-selected`) Overview · Spec · Runs · Terminal · Git;
    Spec renders `/view` with an Edit toggle to the v0.3 editor; Terminal tab hidden when no panel targets it —
    `ui/app.js`, `ui/modules/specs.js`, `ui/modules/specs.css`, `ui/modules/runs.js`, `ui/modules/runs.css`,
    `ui/modules/transcript.js` (pass `{tab}` hints, use tokens)
11. Queued cards (runs module): a queued hand-off badge explains "No Claude session with channels is connected to this
    project — Code-tab sessions don't receive board moves" with Copy prompt / Run headless — `ui/modules/runs.js`

## Interfaces
| Input / output | Validation or consumer | Error states |
|---|---|---|
| `addDrawerPanel(id, title, render, {tab})` | PRD-06 `terminal.js`, runs/specs modules | unknown tab → `overview` (console warning once) |
| `addProjectPanel(id, title, render(box, project))` | core connection panel; PRD-06 project terminal | render throws → panel shows "This panel failed to load" and others still render |
| `window.kanban.connectClaude?(projectId)` | feature-detected here, provided by PRD-06 | rejection → message shown in the panel's status line |
| Daemon routes/events from PRD-03 | welcome, dialog, panel, chip | 400 → message shown inline in the dialog; 403 → "reload the board" hint; 409 on remove → "stop its runs first"; network error → retry banner (v0.3 behaviour) |
| `pick_folder` (PRD-06) | app only | `null` (cancel) → dialog stays open; error → path field shown |

## Acceptance criteria
- [x] Colour literals in PRD-05's files (`ui/board.css`, `ui/app.js`, `ui/board.js`, `ui/index.html`, `ui/modules/{runs,specs}.{js,css}`, `ui/modules/transcript.js`) appear only in the `:root`/dark token blocks; contrast ≥ 4.5:1 for text tokens in both themes (step 2, NFR-02)
- [x] `addDrawerPanel` without a tab lands in Overview; with `{tab: "terminal"}` in Terminal; `addProjectPanel` renders immediately and on project switch (step 3)
- [x] Header shows the five controls; no developer-internal text outside the help menu (step 4, FR-10)
- [x] With no project the welcome shows; with a project, "Connect Claude" is done only when a live interactive session with channels exists (a Code-tab session does not complete it) (step 5, FR-01)
- [x] Add project always shows the dry-run preview with the resolved path before registering; `~` shows the home warning (step 6, FR-02)
- [x] The connection panel updates within 1 s of a session registering or its subscription closing, without reload (step 7, FR-04)
- [x] Without `connectClaude` the panel shows the copy command; with it, the button calls it (step 7, FR-05)
- [x] The Code-tab explanation appears in the connection panel and on queued cards, with Copy prompt / Run headless (steps 7, 11, FR-06)
- [x] At 1280×800 every column is visible or a labelled rail; no silent horizontal overflow (step 8, FR-07)
- [x] Card moves are reachable by keyboard through the "⋯" menu (step 9, FR-09)
- [x] Tabs work with arrow keys and show a visible focus ring; Spec renders by default, Edit keeps v0.3 rules (step 10, FR-08, NFR-02)
- [x] No inline script, no new `innerHTML` sinks beyond the existing `/view` fragment; text via `textContent` (NFR-03)

## Edge cases
| Case | Handling | Priority |
|---|---|---|
| `localStorage` throws (private mode) | rails default to collapsed; no error | must |
| The selected project is removed in another window | `project.removed` → switch to the first remaining project or the welcome | must |
| Onboarding route 404 (daemon older than v0.3.1) | welcome hides the per-project steps; board works | should |
| Add project on a path the daemon refuses | inline 400 message, dialog stays open with the path | must |
| Many sessions (> 10) | panel lists the 10 most recent + "and n more" | could |
| `origin: unknown` | label "unknown" with a tooltip "kind could not be detected" | must |
| Modules registering panels after the drawer is open | drawer re-renders (v0.3 behaviour) | must |
| Window narrower than 1280 | columns scroll horizontally with visible scroll affordance and rails stay labelled | should |

## Out of scope
- `ui/modules/terminal.js`, the native picker and `connectClaude` (PRD-06).
- Daemon routes and events (PRD-03).
- README screenshots (PRD-07).

## Owned files (only these may change)
- `plugins/kanban/ui/index.html`
- `plugins/kanban/ui/app.js`
- `plugins/kanban/ui/board.js`
- `plugins/kanban/ui/board.css`
- `plugins/kanban/ui/modules/runs.js`
- `plugins/kanban/ui/modules/runs.css`
- `plugins/kanban/ui/modules/transcript.js`
- `plugins/kanban/ui/modules/specs.js`
- `plugins/kanban/ui/modules/specs.css`
- `plugins/kanban/tests/test_ui_contract.py`

## Forbidden files
- Other PRDs' owned files (notably `ui/modules/terminal.js`); `.claude/**`; `.SDD/templates/**`

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_tokens_and_no_raw_colours` | colour literals only in token blocks, scoped to PRD-05's files |
| `test_tabs_api_backward_compatible` | `addDrawerPanel` without tab → overview; tab ids accepted |
| `test_project_panel_api` | `addProjectPanel` exported; `window.kanban` not frozen |
| `test_connect_feature_detection` | panel checks `typeof window.kanban.connectClaude === "function"` |
| `test_no_inline_script_or_new_html_sinks` | served UI |
| `test_header_controls_present` | Add project, New ticket, connection chip, help |
| `test_queued_card_explanation` | runs module carries the Code-tab text and both actions |
| `test_add_project_uses_dry_run` | dialog sends `dry_run: true` before the real call |

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py'
python3 -m unittest discover -s tests -p 'test_*.py'
bash .claude/hooks/test_guard_bash.sh
claude plugin validate plugins/kanban && claude plugin validate .
```
Plus a browser check at 1280×800 in light and dark on the local board (Demo evidence).

## Deliverable
Owned files changed; the orchestrator stages them. Report: files, tests (verbatim counts), suggested commit message
`PRD-05: <summary> (KB31-FR-01..10)`, open issues. Blocked → `status: blocked` + blocker type and one line.
