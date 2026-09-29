# ADR-005: UI structure: tokens, collapsible rails, tabbed ticket panel, project panels

| Field | Value |
|---|---|
| Status | Accepted — Decided by kit owner, 2026-09-29 (Q02, Q03); plug-in contract by architect (review 1, finding 3) |
| Date | 2026-09-29 (rev 2) |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-1-onboarding-ux |

## Context
The board overflows at normal widths; the ticket panel is one long scroll; there is no project-level place for
onboarding. The v0.3 plug-in API (`ui/app.js:324-338`, `addDrawerPanel(id, title, render)` at `:329`) is used by
`ui/modules/terminal.js:161` and the runs/specs modules.

## Options considered
| Option | For | Against |
|---|---|---|
| **A. Tokens + Done/E2E rails + tabbed panel + project panels, extending the v0.3 plug-in API compatibly (chosen)** | fixes overflow and density without a framework | touches most UI files |
| B. Adopt a UI framework (React/Svelte) | component model | build step and npm — contradicts the static-UI rule |
| C. Minimal CSS tweaks | small diff | leaves the structural problems |

## Decision
`addDrawerPanel(id, title, render, {tab})` with `tab` ∈ `overview|spec|runs|terminal|git` (default `overview`),
`addProjectPanel(id, title, render)`, and an optional `window.kanban.connectClaude(projectId)` provided by
`terminal.js` and feature-detected by the core UI (architecture §4.1). The core UI never depends on a module; modules
depend on the core API — so the terminal module's work follows the core UI work.

## Consequences
+ legible at 1280×800, accessible; v0.3 modules keep working unchanged.
− one large UI slice (PRD-05); PRD-06 waits for it (wave 3).
- Tests that pin it: `tests/test_ui_contract.py::test_tabs_api_backward_compatible`,
  `::test_project_panel_api`, `::test_connect_feature_detection`, `::test_tokens_and_no_raw_colours` (PRD-05 files);
  `tests/test_terminal_contract.py::test_terminal_sets_connect_before_panel`, `::test_terminal_uses_tokens` (PRD-06).
