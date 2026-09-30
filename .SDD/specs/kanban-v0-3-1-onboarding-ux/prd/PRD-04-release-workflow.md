---
title: PRD-04 - GitHub Actions release workflow, packaging and nested signing
status: done
updated: 2026-09-29
---

# PRD-04: GitHub Actions release workflow, packaging and nested signing

| Field | Value |
|---|---|
| Requirements covered | KB31-FR-12, 14, 18; KB31-NFR-05 |
| ADRs | ADR-002 (uses ADR-001) |
| Wave · Requires | 2 · PRD-01 (`fetch-python.sh`, `python.lock`, `tauri.release.conf.json`) |
| Parallelisable | Yes — wave 2 (with PRD-05) |

> **Developer note (2026-09-29):** PRD-01's extended trim removes the unused `libpython3.12.dylib`, so the signature check targets the Mach-O files that remain (`bin/python3`, `lib-dynload/*.so`, other `lib/*.dylib`) — verified ad-hoc with `codesign -dv`.

## Goal
A tag pushed by the developer tests, builds and drafts a GitHub Release with the universal app whose nested code is
signed (real identity when Apple secrets exist, ad-hoc otherwise); a manual dispatch is a build-only dry run.

## Steps
1. Tests first (see table) — `tests/test_release_workflow.py` (create)
2. Entitlements for the hardened runtime of the app and the bundled interpreter: starts with no exception keys; a key is
   added only when a signed build fails the step-4 smoke test, with a note in ADR-002 — `plugins/kanban/app/src-tauri/entitlements.plist` (create)
3. Release config: add `bundle.macOS.entitlements` → `entitlements.plist` (sequenced after PRD-01 created the file) —
   `plugins/kanban/app/src-tauri/tauri.release.conf.json` (modify)
4. Packaging script `package-release.sh [--host-only]`: requires `resources/python/<arch>` (else exit 1 naming
   `fetch-python.sh`); signs every Mach-O under `src-tauri/resources/python/**` (`bin/python3*`, the remaining `lib/*.dylib`,
   `lib-dynload/*.so`) — with `--options runtime --entitlements entitlements.plist --timestamp -s "$APPLE_SIGNING_IDENTITY"`
   when `SIGNING=1`, else `-s -` (ad-hoc); runs `cargo tauri build --target universal-apple-darwin --bundles app,dmg
   --config src-tauri/tauri.release.conf.json` (host target with `--host-only`); ad-hoc signs the outer bundle when
   `SIGNING` is unset; `codesign --verify --deep --strict --verbose=2 Kanban.app`; smoke test: for each arch the host
   can execute (host arch; x86_64 on Apple Silicon only when Rosetta is available) run
   `Kanban.app/Contents/Resources/python/<arch>/bin/python3 -E -c 'import ssl, sqlite3, ctypes, json'` and fail the
   build on error (this is the only trigger for changing `entitlements.plist`); `ditto -c -k --keepParent` →
   `Kanban.app.zip`; `SHA256SUMS` (shasum -a 256) over `Kanban.dmg` and `Kanban.app.zip`; outputs in `dist/` —
   `plugins/kanban/app/scripts/package-release.sh` (create)
5. Workflow — `.github/workflows/kanban-app-release.yml` (create):
   - triggers `push: tags: ['kanban-v*']` and `workflow_dispatch` (no inputs needed: dispatch is always a dry run);
     no `pull_request`/`pull_request_target`
   - top-level `permissions: contents: read`; `concurrency: {group: kanban-app-release-${{ github.ref }}, cancel-in-progress: false}`
   - job `build` (`runs-on: macos-14`, `permissions: contents: read`): checkout (`persist-credentials: false`) →
     tag/version check **only when** `github.event_name == 'push'` (tag = `kanban-v<v>` and `<v>` equal in
     `plugins/kanban/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `plugins/kanban/scripts/daemon.py`
     `VERSION`, `plugins/kanban/app/src-tauri/tauri.conf.json` `version`, `plugins/kanban/app/src-tauri/Cargo.toml`
     `version`) → Python suites (`plugins/kanban/tests`, `tests/` incl. `test_install.py`, `test_app_python_lock.py`,
     `test_release_workflow.py`) + guard test → Rust toolchain with both apple-darwin targets → `cargo test --locked` →
     `cargo install tauri-cli --locked --version <pinned>` → `fetch-python.sh --arch both` → a step that writes
     `SIGNING=1` to `$GITHUB_ENV` when the event is a tag push and `APPLE_CERTIFICATE` (mapped into the step `env`) is
     non-empty, plus keychain import in that case → `package-release.sh` (Tauri `APPLE_*` env only when `SIGNING=1`) →
     upload `dist/` as an artefact
   - job `release` (`needs: build`, `if: github.event_name == 'push'`, `permissions: contents: write`): no checkout;
     download the artefact; step `env: GH_TOKEN: ${{ github.token }}`;
     `gh release create "$GITHUB_REF_NAME" --repo "$GITHUB_REPOSITORY" --draft --title "$GITHUB_REF_NAME" --notes-file …`
     with the three assets; the notes say "unsigned — see first-launch steps" unless the build reported `SIGNING=1`
     (job output)
   - every `uses:` pinned to a 40-hex commit SHA with a `# vX.Y.Z` comment

## Interfaces
| Input / output | Validation or consumer | Error states |
|---|---|---|
| Tag `kanban-v<version>` (developer) | tag check vs five version fields | mismatch → build job fails before building, no release |
| `workflow_dispatch` | dry run: build + tests + artefact, no signing, no release | — |
| Secrets `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` | only in the build job, only on tag pushes | certificate present but import fails → job fails (no silent unsigned release) |
| `package-release.sh` → `dist/{Kanban.dmg, Kanban.app.zip, SHA256SUMS}` | release job (PRD-02 downloads the zip + sums) | missing python resources → exit 1; codesign verify fails → exit 1 |
| Draft release | developer publishes, then pins sums in `releases.lock` (PRD-07 docs) | a release with the tag already exists → `gh` fails; the developer deletes the draft and re-runs |

## Acceptance criteria
- [x] Triggers are exactly tag push + dispatch; no PR triggers (step 5)
- [x] `build` has `contents: read` and `persist-credentials: false`; `release` has `contents: write`, no checkout step, runs only on tag pushes (step 5)
- [x] Secrets are referenced only in the `build` job and never in an `if:` expression; `SIGNING` comes from a step (step 5)
- [x] Dispatch runs never sign and never reach `release` (step 5)
- [x] Tag/version check step has `if: github.event_name == 'push'` and compares all five fields (step 5)
- [x] Tests (incl. `test_app_python_lock.py`, `test_release_workflow.py`, `cargo test --locked`) run before `fetch-python.sh`, which runs before `package-release.sh` (step 5)
- [x] Every `uses:` has a 40-hex SHA; `tauri-cli` installed `--locked` with a pinned version; a concurrency group is set (step 5)
- [x] `package-release.sh --host-only` on the developer's Mac produces `dist/Kanban.app.zip` + `SHA256SUMS`, and `codesign --verify --deep --strict` passes on the ad-hoc signed app; `codesign -dv` on `Resources/python/arm64/lib/lib-dynload/*.so` shows a signature (step 4)
- [x] After signing, the bundled interpreter of each executable arch passes `-E -c 'import ssl, sqlite3, ctypes, json'`; a failure fails the build (step 4, re-review N5)
- [x] The release step sets `GH_TOKEN: ${{ github.token }}` and passes `--repo "$GITHUB_REPOSITORY"` (step 5, re-review N4)
- [x] Release job creates a **draft** named after the tag with exactly `Kanban.dmg`, `Kanban.app.zip`, `SHA256SUMS` (step 5)

## Edge cases
| Case | Handling | Priority |
|---|---|---|
| Two tags pushed quickly | concurrency group per ref; no cancellation of an in-progress release | must |
| Secrets exist but event is dispatch | not signed (dry run) | must |
| Partial secrets (certificate without notarisation credentials) | signing proceeds; notarisation step fails the job with a message naming the missing secret | should |
| No Rosetta on an Apple Silicon runner | x86_64 smoke test skipped with a log line; arm64 still checked | must |
| Tauri already signed `bundle.resources` (V01) | pre-signed files are re-signed identically; verify still passes | should |
| `SHA256SUMS` line format | `shasum -a 256` two-space format, file names without paths (what PRD-02 parses) | must |
| Runner image update changes Xcode | pinned `runs-on: macos-14`; `cargo --locked` | should |
| Fork without secrets pushes a tag | unsigned draft in the fork | must |

## Out of scope
- Publishing releases, pushing tags, adding Apple secrets (developer).
- The bundled runtime itself (PRD-01); installer download (PRD-02); the version bump and README release docs (PRD-07).
- Homebrew, Linux, Windows.

## Owned files (only these may change)
- `.github/workflows/kanban-app-release.yml`
- `plugins/kanban/app/scripts/package-release.sh`
- `plugins/kanban/app/src-tauri/entitlements.plist`
- `plugins/kanban/app/src-tauri/tauri.release.conf.json` (after PRD-01; adds `bundle.macOS.entitlements` only)
- `tests/test_release_workflow.py`

## Forbidden files
- Other PRDs' owned files; `.claude/**`; `.SDD/templates/**`

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_triggers_and_permissions` | tags + dispatch only; top-level read |
| `test_jobs_split` | build read + `persist-credentials: false`; release write, no checkout, `needs: build`, push-only, `GH_TOKEN` from `github.token`, `--repo "$GITHUB_REPOSITORY"` |
| `test_dispatch_is_dry_run` | no signing and no release on dispatch |
| `test_actions_pinned_by_sha` | every `uses:` has a 40-hex SHA |
| `test_signing_flag_step` | secrets absent from `if:`; `SIGNING` written by a step |
| `test_steps_order` | tests (incl. the two root tests) → fetch → package |
| `test_tag_check_only_on_tags` | the step's `if:` and the five files |
| `test_concurrency` | group set, no cancel-in-progress |
| `test_codesign_verify` | `package-release.sh` runs `codesign --verify --deep --strict` and signs nested Mach-O before the build |
| `test_bundled_python_smoke` | `package-release.sh` runs the `-E -c 'import ssl, sqlite3, ctypes, json'` check per executable arch after signing |
| `test_package_host_only` (local, skipped without cargo) | zip + `SHA256SUMS` produced |

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py'
python3 -m unittest discover -s tests -p 'test_*.py'
bash .claude/hooks/test_guard_bash.sh
claude plugin validate plugins/kanban && claude plugin validate .
bash plugins/kanban/app/scripts/fetch-python.sh --arch arm64 && bash plugins/kanban/app/scripts/package-release.sh --host-only
```

## Deliverable
Owned files changed; the orchestrator stages them. Report: files, tests (verbatim counts), suggested commit message
`PRD-04: <summary> (KB31-FR-12, 14, 18)`, open issues. Blocked → `status: blocked` + blocker type and one line.
