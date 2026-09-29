---
title: PRD-03 - Plugin launcher, session origin, onboarding and project routes
status: todo
updated: 2026-09-29
---

# PRD-03: Plugin launcher, session origin, onboarding and project routes

| Field | Value |
|---|---|
| Requirements covered | KB31-FR-01 (state), 02 (daemon), 04 (data + live), 06 (origin data), 13 (plugin side, Q12); KB31-NFR-03, NFR-01 |
| ADRs | ADR-001 (launcher), ADR-004, ADR-006 |
| Wave · Requires | 1 · — (contract: bundled interpreter path, architecture §2) |
| Parallelisable | Yes — wave 1 (with PRD-01, PRD-02) |

Paths are relative to `plugins/kanban/`.

## Goal
The plugin starts on the app's bundled Python when available; the daemon knows how each session is connected and
announces changes live; the UI can add (with a preview) and remove projects and read/dismiss the welcome state.

## Steps
1. Tests first (see table) — `tests/test_kpython.py` (create), `tests/test_db.py`, `tests/test_daemon.py`,
   `tests/test_daemon_auth.py`, `tests/test_mcp_channel.py` (modify)
2. Launcher `kpython` (POSIX `sh`, no bashisms): candidates `$HOME/Applications/Kanban.app/Contents/Resources/python/<arch>/bin/python3`,
   `/Applications/Kanban.app/…` (macOS only; `<arch>` from `uname -m`), then `python3` on PATH ≥ 3.9
   (`/usr/bin/python3` only when `xcode-select -p` succeeds); each bundled candidate is **run-checked** first
   (`"$py" -E -B -c 'import sys'`) and skipped on failure (next candidate, then system `python3`); a bundled interpreter
   is `exec`ed as `"$py" -E -B "$@"` (ignores `PYTHONHOME`/`PYTHONPATH`; `-B` replaces `PYTHONDONTWRITEBYTECODE`,
   which `-E` drops); a system interpreter gets `PYTHONDONTWRITEBYTECODE=1` and `"$@"`; `--probe` prints
   `bundled <path>` / `system <path>` (after the run-check; a failed bundled candidate is reported on stderr as
   `bundled <path> failed`) or `missing` (exit 0 / 0 / 3);
   `--quiet-missing` exits 0 silently when nothing is found; otherwise missing → one stderr line naming both fixes,
   exit 127 — `scripts/kpython` (create)
3. Plugin entry points call the launcher: `"command": "sh", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/kpython",
   "${CLAUDE_PLUGIN_ROOT}/scripts/server.py"]`; hooks run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/kpython" --quiet-missing
   "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py"` — `.mcp.json`, `hooks/hooks.json` (modify)
4. Additive column: after `migrate` (`kanban_db.py:159-170`), add `sessions.origin TEXT NOT NULL DEFAULT 'unknown'`
   when `PRAGMA table_info(sessions)` lacks it (tolerates "duplicate column" from a concurrent start);
   likewise `sessions.python TEXT NOT NULL DEFAULT 'unknown'` (architect re-review N3); `SCHEMA_VERSION` stays 1
   (`:28`); `register_session` (`:273-281`) takes `origin` (allowed: `cli`, `cli-print`, `code-tab`, `headless`,
   `unknown`) and `python` (allowed: `bundled`, `system`, `unknown`); `remove_project(conn, id)` deletes the project and its session rows only —
   `scripts/kanban_db.py` (modify)
5. Origin classification in `server.py` (next to `channel_from_args`, `:316`): from `parent_args` (`:335`) → `code-tab`
   iff both `--input-format stream-json` and `--output-format stream-json`; else `-p`/`--print` → `cli-print`; else
   `cli`; empty/unreadable → `unknown`; sent in the registration body (`:544-548`) together with `python` —
   `sys.executable` classified `bundled` (under `…/Kanban.app/Contents/Resources/python/`), `system` or `unknown`.
   A **410** answer: the MCP tools keep working on the markdown (local mode), **no embedded UI is started**, and one
   stderr notice says the project was removed from the board and how to re-add it (app → Add project, or
   `daemon.py --open <folder>`) — `scripts/server.py` (modify)
6. Daemon sessions: `h_session` (`daemon.py:634-645`) stores `headless` for derived headless runs, else the sent origin
   if allowed, else `unknown`; refuses with 410 when the project is tombstoned; `session.changed {session, live}` is
   published to the project after registration and after a subscription with a session id opens or closes
   (`Bus.subscribe/unsubscribe`, `:199-210`; published outside the bus lock) — `scripts/daemon.py` (modify)
7. Routes (registered in `_core_routes`, `:598-611`; mutating ones added to `UI_ONLY`, `:76-78`) — `scripts/daemon.py`:
   - `GET /api/projects/{p}/sessions` (read)
   - `GET /api/projects/{p}/onboarding` (read), `POST /api/projects/{p}/onboarding` (ui, UI_ONLY)
   - `POST /api/projects/add` (ui, UI_ONLY)
   - `DELETE /api/projects/{p}` (ui, UI_ONLY)
8. Onboarding flags: human move (`h_move`) sets `onboarding.first_move.<project>` once; dismiss sets
   `onboarding.dismissed.<project>`; `plugin_python` comes from the live sessions' reported `python` (`bundled` if
   any live session runs bundled, else `system`); only while the project has no live session is it a **hint**
   computed in Python with the same order as `kpython` (step 2, cached 30 s) and marked `plugin_python_source:
   "hint"` (else `"session"`) — `scripts/daemon.py`
9. Abuse probe rows for the four new routes (client token → 403 on mutating ones, unmarked path, `..`/relative path,
   `create_specs` on an unmarked folder, delete of an unknown id) — `tests/fixtures/abuse_probe.py`, `tests/test_abuse.py` (modify)

## Interfaces
| Input / output | Validation or consumer | Error states |
|---|---|---|
| `sh kpython [--probe \| --quiet-missing] <script> …` | Claude Code (`.mcp.json`, hooks); tests | missing → 127 + stderr (or 0 silent with `--quiet-missing`); `--probe` missing → exit 3 |
| `POST /api/sessions` body `origin`, `python` | strings; unknown values → `unknown` | tombstoned project → 410 `{"error": "project removed from the board"}` |
| `GET /api/projects/{p}/sessions` → `{sessions: [{id, kind, origin, channel, last_seen, run_id}]}` | PRD-05 connection panel + chip | unknown project → 404 |
| Event `session.changed` `{session: {id, kind, origin, channel}, live: bool}` | PRD-05 (refetch) | — |
| Event `project.removed` `{id}` (to every subscriber) | PRD-05 (switch project) | — |
| `GET /api/projects/{p}/onboarding` → `{connected, first_move, dismissed, plugin_python, plugin_python_source}` | PRD-05 welcome; `connected` = a live `interactive` session with `channel = 1` in this project; `plugin_python` ∈ `bundled`/`system`/`missing`; `plugin_python_source` ∈ `session`/`hint` | unknown project → 404 |
| `POST /api/projects/{p}/onboarding {dismissed: bool}` → the state | PRD-05 | non-bool → 400; client token → 403 |
| `POST /api/projects/add {path: str, create_specs?: bool, dry_run?: bool}` | PRD-05 dialog. `path`: non-empty string ≤ 4096; `~`/`~/…` expanded to the daemon user's home; must be absolute after expansion; resolved (symlinks); existing directory; marker (`.SDD`, `.git`, `.claude`) checked on the folder **as-is before anything is created** | missing/empty/not a string/relative/not a dir → 400; no marker → 400 naming the markers; client token → 403 |
| dry run → `{resolved, name, markers: [...], has_specs, already_registered, is_home, would_create: [".SDD/specs"] \| []}` | shown by the UI before the real call | same 400s; nothing created, nothing published |
| real → `{project: {id, name, root}, resolved, created: [...]}` | clears `project.removed.<id>`; publishes `project.registered` when new | `create_specs` failing (permissions) → 500 with the OS message, registry unchanged |
| `DELETE /api/projects/{p}` → `{removed: id}` | PRD-05 remove action | unknown → 404; live runs (queued/running/waiting) → 409 naming the count; client token → 403 |

## Acceptance criteria
- [ ] `kpython` chooses `~/Applications` bundle, then `/Applications` bundle, then system `python3` (fake `$HOME` + PATH); a bundled interpreter is exec'ed with `-E -B`, a system one with `PYTHONDONTWRITEBYTECODE=1`; missing → 127 with the message, `--quiet-missing` → 0 silent (step 2)
- [ ] A fake bundled python that exits 1 on the run-check → the system `python3` is chosen and `--probe` reports it; with `PYTHONHOME=/bogus` the bundled interpreter still starts (step 2, re-review N1)
- [ ] On a 410 from registration, `server.py` serves the MCP tools on markdown, starts no embedded UI and prints exactly one notice naming both ways to re-add (step 5, re-review N2)
- [ ] A session's `python` report is stored; `plugin_python` follows live sessions and is `source: hint` only while none is live (steps 4, 8, re-review N3)
- [ ] `.mcp.json` and `hooks/hooks.json` invoke `sh …/kpython`; `claude plugin validate plugins/kanban` passes (step 3)
- [ ] Opening a v0.3.0 DB adds `origin` once; a second open is a no-op; `PRAGMA user_version` stays 1; a v0.3.0-style `INSERT INTO sessions (…without origin…)` still succeeds (step 4)
- [ ] argv samples → `code-tab`, `cli-print`, `cli`, `unknown`; a derived headless run stores `headless` whatever was sent (steps 5–6)
- [ ] `session.changed` is received by a project subscriber on registration, subscription open and close (step 6)
- [ ] `POST /api/projects/add` on an existing folder with no marker and `create_specs: true` → 400 and no `.SDD` created (step 7)
- [ ] `dry_run: true` returns the preview, creates nothing and publishes nothing; `path: "~"` → `resolved` = home and `is_home: true` (step 7)
- [ ] Real add with `create_specs: true` on a `.git`-only folder creates only `.SDD/specs/` (step 7)
- [ ] `DELETE` → 409 with a live run; otherwise the project and its sessions are gone, files untouched, `project.removed` published, the project's event streams closed, and a following `POST /api/sessions` for that folder → 410; UI add clears it (step 7)
- [ ] Onboarding: `connected` flips when a channel session subscribes; `first_move` after one UI move; `dismissed` persists across daemon restart (step 8)
- [ ] The hint `plugin_python` agrees with `kpython --probe` for the same fake `$HOME`/PATH (step 8)
- [ ] All four mutating/new routes behave per the abuse probe rows; `test_abuse.py` passes (step 9)

## Edge cases
| Case | Handling | Priority |
|---|---|---|
| DELETE while Claude sessions are live (no runs) | allowed; sessions removed, their subscriptions closed, tombstone set; their `server.py` re-registration gets 410 → local mode with one notice | must |
| Same folder added again after removal | same project id (`project_id_for(root)`), history reattaches, tombstone cleared | must |
| `daemon.py --open <folder>` after removal | `POST /api/projects` (client) re-adds and clears the tombstone (explicit user action) | must |
| Path is a symlink to a project | resolved path registered and shown | must |
| Path is `/` or the home folder | marker rule decides (`~` passes via `~/.claude`); dry run returns `is_home: true` so the UI warns | must |
| Folder already registered | dry run `already_registered: true`; real call returns the project, no event | must |
| Concurrent daemons adding the column | "duplicate column" tolerated | must |
| `ps` unavailable / argv unreadable | `unknown` | must |
| Bundled interpreter damaged or quarantined (fails the run-check) | skipped; system `python3` used; `--probe` says so on stderr | must |
| Session runs an interpreter outside any known location (pyenv, venv) | `python: system` if `sys.executable` is not under a Kanban.app bundle, `unknown` if unreadable | should |
| `/usr/bin/python3` stub without CLT | `kpython` skips it (would open the installer dialog) | should |
| `kpython` without exec bit (plugin cache) | always invoked via `sh` | must |
| A v0.3.0 daemon writes sessions after v0.3.1 added the column | default `unknown` | must |
| Settings keys for a removed project | left in place (harmless), overwritten on re-add | could |

## Out of scope
- All UI rendering of these states (PRD-05); the terminal (PRD-06); the native folder picker (PRD-06).
- The daemon `VERSION` bump (PRD-07).
- Bundling `kpython` into the app (not needed: the app runs the daemon directly).

## Owned files (only these may change)
- `plugins/kanban/scripts/kpython`
- `plugins/kanban/.mcp.json`
- `plugins/kanban/hooks/hooks.json`
- `plugins/kanban/scripts/kanban_db.py`
- `plugins/kanban/scripts/daemon.py` (everything except the `VERSION` line)
- `plugins/kanban/scripts/server.py`
- `plugins/kanban/tests/test_kpython.py`
- `plugins/kanban/tests/test_db.py`
- `plugins/kanban/tests/test_daemon.py`
- `plugins/kanban/tests/test_daemon_auth.py`
- `plugins/kanban/tests/test_mcp_channel.py`
- `plugins/kanban/tests/test_abuse.py`
- `plugins/kanban/tests/fixtures/abuse_probe.py`

## Forbidden files
- Other PRDs' owned files; `plugins/kanban/ui/**`; `.claude/**`; `.SDD/templates/**`

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_kpython_order` / `test_kpython_missing` / `test_kpython_probe` / `test_kpython_env` | step 2 |
| `test_kpython_broken_bundled_falls_back` / `test_kpython_ignores_pythonhome` | fake bundled exits 1 → system chosen; `PYTHONHOME=/bogus` → bundled still starts (N1) |
| `test_mcp_channel.py::test_removed_project_local_mode_no_ui` | 410 → tools work, no UI started, one notice with the re-add hint (N2) |
| `test_session_python_report` | `python` stored; onboarding uses sessions, hint otherwise (N3) |
| `test_origin_column_additive_idempotent` / `test_v030_insert_still_works` | step 4 |
| `test_origin_classification` | argv → code-tab / cli-print / cli / unknown |
| `test_sessions_endpoint` / `test_session_changed_events` | steps 6–7 |
| `test_add_project_unmarked_refused` / `test_add_project_dry_run_changes_nothing` / `test_add_project_home_flag` / `test_add_project_create_specs_only` | step 7 |
| `test_delete_with_live_run_409` / `test_delete_project_tombstone_410` / `test_readd_clears_tombstone` | step 7 |
| `test_onboarding_state` / `test_plugin_python_parity` | step 8 |
| `test_new_routes_ui_only` | client token 403 on the mutating routes |

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py'
python3 -m unittest discover -s tests -p 'test_*.py'
bash .claude/hooks/test_guard_bash.sh
claude plugin validate plugins/kanban && claude plugin validate .
```

## Deliverable
Owned files changed; the orchestrator stages them. Report: files, tests (verbatim counts), suggested commit message
`PRD-03: <summary> (KB31-FR-01, 02, 04, 06, 13)`, open issues. Blocked → `status: blocked` + blocker type and one line.
