# Kanban v0.3.1 — Open questions

Register only: one row per question. Record each decision in its row and in the ADR/PRD it changes, as
"Decided by <role>, <date>".

## Scope
| # | Question | Owner | Status | Decision / next step |
|---|---|---|---|---|
| Q01 | The "New ticket" form of v0.2 is missing in v0.3 (regression). Fix it inside v0.3 before its hand-off, or in v0.3.1? | kit owner | Decided | Decided by kit owner, 2026-09-29: fixed in v0.3 as QA bug B14 before its hand-off; KB31-FR-03 then only covers its place in the new header |
| Q02 | Visual direction for the design pass: keep the current neutral look and tidy it, or a distinct identity (brand colour, type scale)? | kit owner | Decided | Decided by kit owner, 2026-09-29: tidy the neutral look with design tokens; no brand identity |
| Q03 | Board width: collapse Done/E2E by default, or fit all 8 columns by narrowing them? | kit owner | Decided | Decided by kit owner, 2026-09-29: Done and E2E collapsed to thin columns with counts, expandable |
| Q04 | Should "Add project" also offer to scaffold `.SDD/specs` (and the kit) in a folder that has only `.git`? | kit owner | Decided | Decided by kit owner, 2026-09-29: offer to create `.SDD/specs` only; the kit stays `install.sh` |
| Q05 | Signing of downloadable builds | kit owner | Decided | Decided by kit owner, 2026-09-29: unsigned now, signing-ready (steps activate when Apple secrets are added) |
| Q06 | Python on users' Macs | kit owner | Decided | Decided by kit owner, 2026-09-29: bundle a relocatable Python in the app |
| Q07 | Where the release work lives | kit owner | Decided | Decided by kit owner, 2026-09-29: in v0.3.1 (this ticket); tier upgraded to Full |
| Q08 | Architectures | kit owner | Decided | Decided by kit owner, 2026-09-29: universal (Apple Silicon + Intel) |
| Q10 | App via the `get.sh` one-liner | kit owner | Decided | Decided by kit owner, 2026-09-29: offered in the installer menu, pre-selected on macOS with `kanban`; `--yes` includes it; `--update` refreshes it; downloads (checksum-verified) instead of building; amends v0.3 PRD-08 'never downloads' for the app only |
| Q11 | Which release the installer downloads | kit owner | Decided | Decided by kit owner, 2026-09-29: the release matching the kit/plugin version, fallback latest |
| Q09 | Which relocatable CPython build (e.g. python-build-standalone, pinned) and its licence handling | dev | Decided | Decided by architect, 2026-09-29: python-build-standalone `install_only_stripped` CPython 3.12, one per arch, pinned in `app/python.lock` — see ADR-001; licence handling per Q13 |
| Q12 | Which Python do the plugin's MCP server and hooks use (review finding 15: `.mcp.json` and `hooks/hooks.json` call bare `python3`, so a clean Mac with only Kanban.app still has no board tools)? | kit owner | Decided | Decided by kit owner, 2026-09-29: they start through a small POSIX `sh` launcher `plugins/kanban/scripts/kpython` that uses Kanban.app's bundled Python when the app is installed (`~/Applications/Kanban.app` or `/Applications/Kanban.app`, arch-specific path), else system `python3`; `.mcp.json` and `hooks/hooks.json` call the launcher. `get.sh` still needs `python3` (unchanged). Applied in ADR-001, PRD-03 |
| Q13 | Licences of the bundled runtime: where do they live and are they copied into projects? | kit owner | Decided | Decided by kit owner, 2026-09-29: the runtime's licence files ship inside Kanban.app (never pruned; a test checks them), are listed in `LICENSES/NOTICE.md`, and are not copied into user projects by `install.py`. Applied in ADR-001, PRD-01, PRD-02, PRD-07; amends KB31-NFR-01 |
| Q14 | Authenticity of downloaded releases while builds are unsigned | kit owner | Decided | Decided by kit owner, 2026-09-29: after publishing a release the developer commits its SHA256SUMS into the kit at `plugins/kanban/app/releases.lock` (tag → sha256 per asset); the installer trusts pinned sums over the release's own `SHA256SUMS` when an entry exists and refuses on mismatch; without a pinned entry it falls back to the release's `SHA256SUMS` and says so. Applied in ADR-003, PRD-02, PRD-07; amends KB31-FR-16 |
| Q15 | Installer: does `--all` include the app, and which repository's releases does a fork download? | kit owner | Decided | Decided by kit owner, 2026-09-29: `--all` includes the app download on macOS; the release base URL is derived from `AGENTIC_KIT_REPO` (`github.com/<owner>/<repo>`) so forks download their own releases. Applied in ADR-003, PRD-02; amends KB31-FR-16, 17 |

| Q18 | Licence texts of libraries statically linked into the bundled Python (OpenSSL Apache-2.0, SQLite, libffi, zlib, bzip2, xz, mpdecimal, expat, ncurses, libedit, …) — the archive ships only CPython's LICENSE.txt | kit owner | Decided | Decided by kit owner, 2026-09-30: ship the component licence texts inside Kanban.app — collected from upstream at the versions python-build-standalone 20260924 uses, pinned in the repo, copied next to CPython's licence by `fetch-python.sh`, checked by a test; NOTICE.md lists them · amends Q13, PRD-01, PRD-07 |

## UI
| # | Question | Owner | Status | Decision / next step |
|---|---|---|---|---|
| Q16 | Where is "welcome dismissed" stored? | architect | Decided | Decided by architect, 2026-09-29: daemon settings key `onboarding.dismissed.<project id>` (not `localStorage`), because the app window and a browser tab on the same board have separate storage and the welcome must not reappear in one after it was dismissed in the other — ADR-006, PRD-03 |
| Q17 | How does the connection panel update live? | architect | Decided | Decided by architect, 2026-09-29: a `session.changed` event on registration, subscription open and close (no polling) — ADR-004, PRD-03, PRD-05 |

## Testing / developer verification
| # | Question | Owner | Status | Decision / next step |
|---|---|---|---|---|
| V01 | Does Tauri 2 sign Mach-O files under `bundle.resources` (`Resources/python/**`) when it signs the bundle? | dev | Open | PRD-04 pre-signs them regardless; the developer checks `codesign -dv` on a nested `libpython3.12.dylib` in the first signed build and records the answer here |
| V02 | Does `tauri-build` (build.rs) or `cargo tauri build` fail when a `bundle.resources` glob matches nothing? | dev | Open | Mitigated by design: python resources are only in `tauri.release.conf.json` (PRD-01); the developer confirms with `cargo test` on a clean checkout without `resources/python/` |
| V03 | Which host serves release-asset redirects today (`objects.githubusercontent.com` or `release-assets.githubusercontent.com`)? | dev | Open | Both are in the allow-list (ADR-003); the developer notes the observed hop chain in the E2E report |
| V04 | Does the desktop app's Code-tab `claude` still start with `--input-format stream-json --output-format stream-json`? | dev | Open | Assumption of ADR-004 (seen 2026-09-28); re-check in Demo with `ps -ww -o args=` on the Code-tab process |
