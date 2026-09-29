# ADR-004: Session origin label from the parent argv (additive column, no schema bump)

| Field | Value |
|---|---|
| Status | Accepted — Decided by architect, 2026-09-29 (review 1, findings 10, 19, 24) |
| Date | 2026-09-29 (rev 2) |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-1-onboarding-ux |

## Context
The connection panel should say whether a session is a CLI session with or without channels, a print-mode CLI call, a
desktop Code-tab session or a headless run. `server.py:335` (`parent_args`) already reads the parent argv and
`:531-550` registers the session; the sessions table (`kanban_db.py:51-54`) has no origin. `migrate`
(`kanban_db.py:159-170`) refuses a DB whose `user_version` is newer, so a schema bump would lock a v0.3.0 daemon (e.g.
an older app still installed) out of the DB.

## Options considered
| Option | For | Against |
|---|---|---|
| **A. Classify the parent argv at registration; additive `sessions.origin` column added idempotently, `user_version` unchanged (chosen)** | no user input; v0.3.0 still opens the DB | schema not versioned for this column |
| B. Same, as migration 1→2 (`user_version` 2) | the migration ladder records it | a v0.3.0 daemon refuses the DB after any v0.3.1 start |
| C. Ask the user to name sessions | exact | friction; stale |
| D. Don't show origin | simplest | the Code-tab limitation stays invisible |

## Decision
After `migrate`, `kanban_db` adds `origin TEXT NOT NULL DEFAULT 'unknown'` to `sessions` when `PRAGMA table_info`
lacks it (idempotent, safe under concurrent starts). `server.py` sends `origin`: both `--input-format stream-json` and
`--output-format stream-json` → `code-tab`; else `-p`/`--print` → `cli-print`; else `cli`; unreadable → `unknown`. The
daemon stores `headless` for derived headless runs, else the sent value if allowed, else `unknown`. The same additive
mechanism adds `sessions.python` (`bundled`/`system`/`unknown`, from `server.py`'s `sys.executable`), which the welcome
uses for `plugin_python` (architect re-review N3). Live changes are
announced with a `session.changed` event (registration, subscription open/close). Labels only.

## Consequences
+ explains the Code-tab limitation where it matters; downgrade-safe.
− the column is outside the `user_version` ladder (a later real migration must tolerate it existing); a Claude Code
  launch-flag change could mislabel (shows `unknown`/`cli`; V04).
- Tests that pin it: `test_db.py::test_origin_column_additive_idempotent`, `test_db.py::test_v030_insert_still_works`,
  `test_mcp_channel.py::test_origin_classification`, `test_daemon.py::test_sessions_endpoint`,
  `test_daemon.py::test_session_changed_events`.
