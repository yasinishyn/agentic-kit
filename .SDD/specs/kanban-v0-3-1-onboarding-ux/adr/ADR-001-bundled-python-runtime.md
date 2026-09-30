# ADR-001: Bundled Python runtime: python-build-standalone, both arches, pruned; plugin launcher

| Field | Value |
|---|---|
| Status | Accepted — Decided by kit owner, 2026-09-29 (Q06, Q08, Q12, Q13); build choice decided by architect, 2026-09-29 (Q09) |
| Date | 2026-09-29 (rev 2 after architect review 1) |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-1-onboarding-ux |

## Context
The board service is Python; the app resolves only system interpreters (`app/src-tauri/src/python.rs:4` `FALLBACKS`,
`shell.rs:84-99`) and the plugin runs bare `python3` (`.mcp.json`, `hooks/hooks.json`), so a clean Mac without the
Command Line Tools cannot run either. The owner chose to bundle Python in a universal app (Q06, Q08) and to let the
plugin use it (Q12).

## Options considered
| Option | For | Against |
|---|---|---|
| **A. python-build-standalone `install_only_stripped` 3.12, arm64 + x86_64, pruned (chosen)** | relocatable, published sha256, ~25 MB compressed each, widely used (uv) | two copies in a universal app; third-party build |
| B. python.org universal2 framework | one universal build, official | not designed to be relocated into an app bundle; installer-based, hard to automate |
| C. Freeze the daemon (PyInstaller-style) | one binary per arch | freezer is a non-stdlib build dependency; plug-ins (`routes_*.py`) loaded dynamically fight freezing |
| D. Require system python3 + guide | smallest app | owner chose bundling (Q06) |

## Decision
`app/python.lock` pins CPython 3.12.x, the build date, per-arch URL + sha256 and the licence file paths.
`app/scripts/fetch-python.sh` verifies before unpacking into `src-tauri/resources/python/{arm64,x86_64}`, prunes
(tests, idlelib, tkinter, turtledemo, ensurepip, lib2to3, pydoc_data, tcl8.6/tk8.6 and their libs, `include/`,
`config-*`, static `libpython*.a`, shipped `__pycache__`) but **never licence files**, then precompiles the stdlib with
`compileall --invalidation-mode unchecked-hash`; ceiling ≤ 45 MB unpacked per arch (changing it needs a note here).
Python resources are listed only in `tauri.release.conf.json`, so default builds and `cargo test` need no fetch. The app
prefers `Resources/python/<arch>/bin/python3` and runs the daemon on it with `-E -B` (environment `PYTHONHOME`/
`PYTHONPATH` ignored, no bytecode writes). The plugin's MCP server and hooks start through `scripts/kpython` (POSIX
`sh`): the bundled interpreter of an installed Kanban.app (`~/Applications`, then `/Applications`) if it passes a
run-check (`-E -B -c 'import sys'`), exec'ed with `-E -B`; else `python3` ≥ 3.9 on PATH. Licence files ship inside the bundle, are
listed in `LICENSES/NOTICE.md` and are not copied into projects (Q13).

## Consequences
+ the app and the plugin work on a clean Mac once Kanban.app is in Applications; one place to bump (`python.lock`).
− ≤ 90 MB of runtimes in a universal app; `get.sh` itself still needs `python3` (Q12); a Kanban.app run from outside
  Applications does not serve the plugin (welcome shows `plugin_python: missing`).
- Tests that pin it: `tests/test_app_python_lock.py` (lock format, bad sum refused, prune list, licence files kept,
  size ceiling, `.pyc` present), cargo `python_resolution_prefers_bundled` and `daemon_command_isolated_bundled`
  (`python.rs`, `shell.rs`), `plugins/kanban/tests/test_kpython.py` (order, run-check fallback, `PYTHONHOME` ignored, missing,
  `--probe`, parity with the daemon's `plugin_python` hint).

**Measured 2026-09-29 (real archives):** 38.5–39 MB per arch after the extended trim (Tcl/Tk 9.0, pip, share, pkgconfig, unused libpython dylib, dev launchers, symlinked duplicates); `ssl`, `sqlite3`, `ctypes`, `json` import under `-E -B`; app bundle 84 MB.
