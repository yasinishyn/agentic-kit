---
title: PRD-01 - Bundled Python runtime and app resolution
status: todo
updated: 2026-09-29
---

# PRD-01: Bundled Python runtime and app resolution

| Field | Value |
|---|---|
| Requirements covered | KB31-FR-13 (app side); KB31-NFR-01, 05 |
| ADRs | ADR-001 |
| Wave · Requires | 1 · — |
| Parallelisable | Yes — wave 1 (with PRD-02, PRD-03) |

Paths are relative to `plugins/kanban/`; repo-root paths start with `/`.

## Goal
A pinned, pruned, precompiled CPython for both arches that release builds bundle (and default builds do not), and an
app that prefers it and never writes bytecode into its bundle.

## Steps
1. Lock file with CPython 3.12.x python-build-standalone `install_only_stripped` for `aarch64-apple-darwin` and
   `x86_64-apple-darwin`: version, build date, per-arch URL + sha256, `licence_files` (paths inside the unpacked tree),
   `max_unpacked_mb: 45` — `app/python.lock` (create)
2. Fixture: a tiny `.tar.gz` shaped like an `install_only_stripped` archive (a fake `bin/python3` shell script that
   answers `--version` and `-m compileall`, stdlib dirs to prune, licence files) + its sha256 —
   `/tests/fixtures/fake_python/**` (create)
3. Tests first (see table) — `/tests/test_app_python_lock.py` (create)
4. Fetch script `fetch-python.sh [--arch arm64|x86_64|both] [--lock <file>] [--dest <dir>]`: download over HTTPS
   (`curl --proto '=https' --fail`), verify sha256 **before** unpacking, unpack into a temp dir, prune, keep licence
   files, precompile (`python3 -m compileall -q -j0 --invalidation-mode unchecked-hash`, run with the host-arch
   bundled interpreter for both trees), enforce the size ceiling, then move into
   `src-tauri/resources/python/<arch>/`; `file://` URLs accepted only when `--lock` is given (tests) —
   `app/scripts/fetch-python.sh` (create)
5. Release-only Tauri config merged by `cargo tauri build --config`: adds
   `"resources/python/arm64/**": "python/arm64/"` and `"resources/python/x86_64/**": "python/x86_64/"` to
   `bundle.resources` (the default `tauri.conf.json` stays unchanged) — `app/src-tauri/tauri.release.conf.json` (create)
6. Ignore fetched runtimes: `src-tauri/resources/python/` — `app/.gitignore` (modify)
7. Resolution: `python::bundled(resource_dir, arch)` → `<resource_dir>/python/<arm64|x86_64>/bin/python3`
   (`aarch64`→`arm64`); `candidates` puts it first when the file exists, then the v0.3 order (`python.rs:14-25`) —
   `app/src-tauri/src/python.rs` (modify)
8. Shell: `find_python` (`shell.rs:84-99`) receives the resource dir (`shell.rs:423`) and passes the bundled candidate;
   error text names the bundled path first and says the app bundle may be damaged when it is missing in a release
   build; `daemon_command` (`shell.rs:101-105`) runs a bundled interpreter as `python3 -E -B daemon.py …` (`-E` ignores
   `PYTHONHOME`/`PYTHONPATH` from the environment, `-B` replaces `PYTHONDONTWRITEBYTECODE`, which `-E` would drop) and
   still sets `PYTHONDONTWRITEBYTECODE=1` for system interpreters — `app/src-tauri/src/shell.rs` (modify: python and
   daemon-env parts only)

## Interfaces
| Input / output | Validation or consumer | Error states |
|---|---|---|
| `app/python.lock` (JSON) | read by `fetch-python.sh`, `/tests/test_app_python_lock.py`, `/LICENSES/NOTICE.md` (PRD-07) | missing arch, non-https URL, host ≠ `github.com/astral-sh/python-build-standalone`, sha256 not 64-hex → test fails |
| `fetch-python.sh --arch <a>` | consumed by CI (PRD-04) and developers | unknown arch → exit 2 + usage; download fails → exit 1, dest untouched; sha256 mismatch → exit 1, nothing unpacked; a licence file missing after prune → exit 1; unpacked size > `max_unpacked_mb` → exit 1 with the measured size; compileall fails → exit 1 |
| `tauri.release.conf.json` | merged by `package-release.sh` (PRD-04) | resources absent → `cargo tauri build` fails (release build only) |
| `python::bundled(resource_dir, arch) -> Option<PathBuf>` | `shell.rs::find_python` | file absent → `None` (falls through to system candidates) |
| Daemon env | `daemon.py` started by the app | — |

## Acceptance criteria
- [ ] `python.lock` pins both arches; URLs are https on `github.com/astral-sh/python-build-standalone`; sha256 64-hex (step 1)
- [ ] `fetch-python.sh` refuses a wrong sum before writing anything (dest dir absent afterwards) (step 4)
- [ ] After a fetch, none of: `test/`, `tests/` under the stdlib, `idlelib`, `tkinter`, `turtledemo`, `ensurepip`, `lib2to3`, `pydoc_data`, `lib/tcl8.6`, `lib/tk8.6`, `libtcl*`, `libtk*`, `include/`, `lib/python3.12/config-*`, `libpython*.a` exist; every `licence_files` entry exists (step 4, Q13)
- [ ] Every stdlib `.py` kept has an unchecked-hash `.pyc` in `__pycache__` (step 4)
- [ ] Unpacked tree per arch ≤ 45 MB, measured and printed by the script; the ceiling changes only with an ADR-001 note (step 4)
- [ ] `tauri.conf.json` still contains no `python` resource; `cargo test` and `cargo tauri build --bundles app` pass on a checkout without `resources/python/` (steps 5–6, V02)
- [ ] `python.rs`: the bundled interpreter is candidate 0 for `aarch64` and `x86_64` when present; absent → v0.3 order unchanged (step 7)
- [ ] With the bundled interpreter the daemon command's args start with `-E -B`; with a system interpreter the env carries `PYTHONDONTWRITEBYTECODE=1` (cargo test on the built `Command`) (step 8, architect re-review N1)
- [ ] Local: `fetch-python.sh --arch arm64` + `cargo tauri build --bundles app --config src-tauri/tauri.release.conf.json` → the running daemon's `ps -o args=` shows `…/Kanban.app/Contents/Resources/python/arm64/bin/python3` and `find Kanban.app -newer <launch marker> -name '*.pyc'` is empty after a launch

## Edge cases
| Case | Handling | Priority |
|---|---|---|
| Upstream archive re-published with a different sum | sha256 mismatch → exit 1; bump `python.lock` deliberately | must |
| Network failure mid-download | temp file discarded, dest untouched, exit 1 | must |
| Existing `resources/python/<arch>` from an older lock | replaced only after the new tree passed all checks (temp dir + rename) | must |
| Running on Intel without Rosetta for arm64 tree (or vice versa) | compileall uses the host-arch interpreter for both trees (bytecode is arch-independent) | must |
| `PYTHONHOME`/`PYTHONPATH` set in the user's environment | ignored for the bundled interpreter (`-E`) | must |
| Bundled interpreter present but fails `--version` (damaged bundle) | treated like any failing candidate; next candidate tried; error screen names the bundled path | must |
| App run under Rosetta (`ARCH` = `x86_64` on Apple Silicon) | x86_64 runtime chosen, which exists in a universal bundle | should |
| Upstream renames a licence file | `licence_files` check fails the fetch; update the lock | should |
| Size creeps over 45 MB after a Python bump | fetch fails with the measured size; raise only with an ADR-001 note | should |

## Out of scope
- CI workflow, packaging, signing and entitlements (PRD-04).
- The plugin launcher `kpython`, `.mcp.json`, `hooks/hooks.json` (PRD-03).
- The folder picker and any other `shell.rs` command (PRD-06); the app version in `tauri.conf.json`/`Cargo.toml` (PRD-07).
- `NOTICE.md` rows for the runtime licences (PRD-07).

## Owned files (only these may change)
- `plugins/kanban/app/python.lock`
- `plugins/kanban/app/scripts/fetch-python.sh`
- `plugins/kanban/app/src-tauri/tauri.release.conf.json`
- `plugins/kanban/app/src-tauri/src/python.rs`
- `plugins/kanban/app/src-tauri/src/shell.rs` (python resolution, error strings, resource dir, `daemon_command` env)
- `plugins/kanban/app/.gitignore`
- `tests/test_app_python_lock.py`
- `tests/fixtures/fake_python/**`

## Forbidden files
- Other PRDs' owned files, notably `tauri.conf.json`, `Cargo.toml`, `Cargo.lock`, `build.rs`, `scope.rs`; `.claude/**`; `.SDD/templates/**`

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_lock_format` | both arches, https on astral-sh, 64-hex sums, `licence_files` non-empty, `max_unpacked_mb` = 45 |
| `test_fetch_refuses_bad_sum` | fixture with a wrong sum → exit 1, dest absent |
| `test_prune_list_and_licences_kept` | pruned paths absent, licence files present |
| `test_precompiled_unchecked_hash` | `.pyc` present; flags field = unchecked-hash |
| `test_size_ceiling` | fixture with a lowered ceiling → exit 1 with the size |
| `test_default_config_has_no_python` | `tauri.conf.json` resources contain no `python`; release config does |
| `python_resolution_prefers_bundled` (cargo) | bundled first for both arches; absent → v0.3 order |
| `daemon_command_isolated_bundled` (cargo) | bundled → args `-E -B daemon.py …`; system → `PYTHONDONTWRITEBYTECODE=1` |

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py'
python3 -m unittest discover -s tests -p 'test_*.py'
bash .claude/hooks/test_guard_bash.sh
claude plugin validate plugins/kanban && claude plugin validate .
source "$HOME/.cargo/env" && (cd plugins/kanban/app/src-tauri && cargo test) && (cd plugins/kanban/app && cargo tauri build --bundles app)
```

## Deliverable
Owned files changed; the orchestrator stages them. Report: files, tests (verbatim counts), suggested commit message
`PRD-01: <summary> (KB31-FR-13)`, open issues. Blocked → `status: blocked` + blocker type and one line.
