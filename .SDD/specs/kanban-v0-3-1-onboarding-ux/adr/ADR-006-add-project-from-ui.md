# ADR-006: Add and remove projects from the UI

| Field | Value |
|---|---|
| Status | Accepted — Decided by kit owner, 2026-09-29 (Q04); preview, tombstone and onboarding storage by architect (review 1, findings 13, 14, 18; Q16) |
| Date | 2026-09-29 (rev 2) |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-1-onboarding-ux |

## Context
Projects are registered only by Claude sessions (`daemon.py:634` `h_session`) or `daemon.py --open`
(`POST /api/projects`, client scope, `:601`); the marker rule is `project_root` (`daemon.py:361-370`). The app has no
way to add or remove one.

## Options considered
| Option | For | Against |
|---|---|---|
| **A. UI-token route with the marker rule, dry-run preview, optional `.SDD/specs` creation, native picker in the app; remove with a tombstone (chosen)** | discoverable; same safety rules; removal sticks | new UI-scope routes; a tombstone setting |
| B. Reuse the client-scope `POST /api/projects` from the UI | no new route | would give the UI token client powers or the page the client token |
| C. Remove without a tombstone | simpler | an open Claude session re-registers the project within seconds |

## Decision
`POST /api/projects/add {path, create_specs?, dry_run?}` and `DELETE /api/projects/{p}`, both in `UI_ONLY`
(`daemon.py:76-78`). The marker rule applies to the folder as-is before anything is created; `dry_run` reports and
changes nothing; `create_specs` creates `.SDD/specs` and nothing else. Delete refuses with live runs, removes the
registry rows, writes `project.removed.<id>`, closes the project's subscriptions and publishes `project.removed`; while
tombstoned `POST /api/sessions` answers 410; the UI add route and `POST /api/projects` clear it. Onboarding flags
(`first_move`, `dismissed`) are daemon settings per project (Q16), not browser storage.

## Consequences
+ first-run works from the app alone; removal is predictable.
− two more mutating routes (covered by the abuse probe); a removed project reappears only by an explicit add.
- Tests that pin it: `test_daemon.py::test_add_project_dry_run_changes_nothing`, `::test_add_project_unmarked_refused`,
  `::test_add_project_create_specs_only`, `::test_delete_project_tombstone_410`, `::test_delete_with_live_run_409`,
  `::test_onboarding_state`; `test_daemon_auth.py::test_new_routes_ui_only`; `fixtures/abuse_probe.py` rows.
