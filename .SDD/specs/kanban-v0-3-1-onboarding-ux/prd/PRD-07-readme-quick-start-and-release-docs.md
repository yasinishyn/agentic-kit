---
title: PRD-07 - README quick start, release docs and version 0.3.1
status: done
updated: 2026-09-29
---

# PRD-07: README quick start, release docs and version 0.3.1

| Field | Value |
|---|---|
| Requirements covered | KB31-FR-11, 15; KB31-NFR-01 (NOTICE rows, Q13) |
| ADRs | ADR-001, ADR-002, ADR-003, ADR-004 |
| Wave · Requires | 4 · PRD-01, 02, 03, 04, 05, 06 (screenshots need the final UI; version fields sequenced after their owners) |
| Parallelisable | No — wave 4 |

## Goal
Docs that take a new user from zero to a working board (download, one-liner or source), maintainer release steps
including pinning sums, runtime licence notices, and version 0.3.1 in all five places.

## Steps
1. Tests first — `tests/test_versions_and_docs.py` (create)
2. Version 0.3.1 in all five places: `plugins/kanban/.claude-plugin/plugin.json` (`:4`),
   `.claude-plugin/marketplace.json` (`:9`), `plugins/kanban/scripts/daemon.py` `VERSION` (`:61`, the line only),
   `plugins/kanban/app/src-tauri/tauri.conf.json` `version` (`:4`), `plugins/kanban/app/src-tauri/Cargo.toml`
   `version` (`:3`) and the root package entry in `Cargo.lock` (modify)
3. Screenshots from the synthetic demo project (welcome, Add project preview, connection panel, first move, light +
   dark board at 1280×800) — `plugins/kanban/docs/img/**` (create)
4. Plugin README — `plugins/kanban/README.md` (modify):
   - Quick start: (a) `get.sh` one-liner (app included on macOS; `python3` still needed for the installer), (b) download
     the `.dmg` from Releases and verify `SHA256SUMS`, (c) build from source (Rust/Tauri/Xcode commands; no bundled
     Python unless `fetch-python.sh` + the release config)
   - First launch of an unsigned app: macOS 15+ — open once, then System Settings → Privacy & Security → "Open
     Anyway"; macOS 14 and earlier — right-click → Open; any version — `xattr -dr com.apple.quarantine
     /Applications/Kanban.app`; the installer path needs none of this
   - First-run walkthrough with the screenshots: welcome → Add project → Connect Claude → first move
   - Troubleshooting: plugin can't find Python (move Kanban.app to Applications or install python3; `kpython --probe`),
     port taken, no channel events, Code-tab sessions, Gatekeeper, "approved before v0.3", **older app after a
     newer one ran** (v0.3.0 ignores the new `sessions.origin` column; no action needed), project removed but a
     session still open (410 → local mode; add it again)
   - Releasing (maintainers): bump the five versions, tag `kanban-v<version>`, push, optional `workflow_dispatch` dry
     run first, review and publish the draft, then commit its `SHA256SUMS` into `plugins/kanban/app/releases.lock`
     (Q14); adding Apple secrets for signing
5. Root README: component table and the one-liner mention the app (download on macOS, `--all` includes it) — `README.md` (modify)
6. `LICENSES/NOTICE.md`: rows for python-build-standalone and CPython (PSF) with the licence file paths inside
   `Kanban.app` from `python.lock` `licence_files`, stating they ship in the app and are not copied into projects
   (Q13) — `LICENSES/NOTICE.md` (modify)

## Interfaces
| Input / output | Validation or consumer | Error states |
|---|---|---|
| Five version fields | PRD-04 CI tag check; `daemon.py --ensure` version comparison; installer tag | any mismatch → `test_versions_match` fails |
| README relative links and images | readers; `test_readme_links_resolve` | missing file → test fails |
| `NOTICE.md` runtime rows | readers; `test_notice_lists_runtime_licences` (reads `python.lock`) | missing row → test fails |

## Acceptance criteria
- [x] All five versions (and the `Cargo.lock` root package) read 0.3.1; `test_versions_match` passes (step 2)
- [x] Quick start covers the three install routes; the Gatekeeper section names the macOS 15+ "Open Anyway" route and `xattr` (step 4, FR-15)
- [x] Troubleshooting has the rows listed in step 4, including the older-app/schema row (step 4, FR-11)
- [x] Releasing section includes pinning `SHA256SUMS` into `releases.lock` and the dispatch dry run (step 4)
- [x] Every image and relative link in both READMEs resolves (step 3–5)
- [x] `NOTICE.md` lists every `licence_files` entry of `python.lock` (step 6)
- [x] `cargo test --locked` still passes after the version bump (step 2)

## Edge cases
| Case | Handling | Priority |
|---|---|---|
| Screenshots contain real paths or names | synthetic demo project under a neutral path only | must |
| Dark-mode screenshots unreadable in the README | light screenshots in the text, dark in a collapsed details block | could |
| Version bumped before a PRD's file changed | wave 4 only; the test runs in CI too | must |
| `releases.lock` has no entry yet for 0.3.1 at release time | docs say the entry is added after publishing; the installer notes unpinned sums until then | must |

## Out of scope
- Any behaviour change; publishing the release and adding secrets (developer).
- Translating docs; a website.

## Owned files (only these may change)
- `plugins/kanban/README.md`
- `README.md`
- `LICENSES/NOTICE.md`
- `plugins/kanban/.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`
- `plugins/kanban/scripts/daemon.py` (the `VERSION` line only; after PRD-03)
- `plugins/kanban/app/src-tauri/tauri.conf.json` (`version` only)
- `plugins/kanban/app/src-tauri/Cargo.toml` (`version` only; after PRD-06)
- `plugins/kanban/app/src-tauri/Cargo.lock` (root package version only; after PRD-06)
- `plugins/kanban/docs/img/**`
- `tests/test_versions_and_docs.py`

Amended by Q18 (2026-09-30): `LICENSES/NOTICE.md` lists the component licences shipped inside Kanban.app (from
PRD-01's `app/licences/python-runtime/`), and records why zlib, ncurses and libedit are not shipped (macOS system
libraries).

## Forbidden files
- Other PRDs' owned files beyond the listed lines; `.claude/**`; `.SDD/templates/**`

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_versions_match` | plugin.json = marketplace = daemon `VERSION` = tauri.conf.json = Cargo.toml (= Cargo.lock root) |
| `test_readme_links_resolve` | images and relative links exist |
| `test_notice_lists_runtime_licences` | every `python.lock` `licence_files` entry named in NOTICE |
| `test_readme_has_gatekeeper_and_troubleshooting` | "Open Anyway", `xattr`, the troubleshooting rows |

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py'
python3 -m unittest discover -s tests -p 'test_*.py'
bash .claude/hooks/test_guard_bash.sh
claude plugin validate plugins/kanban && claude plugin validate .
source "$HOME/.cargo/env" && (cd plugins/kanban/app/src-tauri && cargo test --locked)
```

## Deliverable
Owned files changed; the orchestrator stages them. Report: files, tests (verbatim counts), suggested commit message
`PRD-07: <summary> (KB31-FR-11, 15)`, open issues. Blocked → `status: blocked` + blocker type and one line.
