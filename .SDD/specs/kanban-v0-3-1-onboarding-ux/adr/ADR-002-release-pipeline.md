# ADR-002: Release pipeline: GitHub Actions, split jobs, draft releases, signing-ready

| Field | Value |
|---|---|
| Status | Accepted — Decided by kit owner, 2026-09-29 (Q05, Q07, Q08); job structure by architect (review 1, findings 8, 11, 12) |
| Date | 2026-09-29 (rev 2) |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-1-onboarding-ux |

## Context
Users must download the app without Rust/Xcode; `.github/` does not exist yet. Agents never push tags or publish (kit
hard rule). The bundle contains third-party Mach-O files (`Resources/python/**`) that must be signed for notarisation.

## Options considered
| Option | For | Against |
|---|---|---|
| **A. Tag-triggered GitHub Actions on macos-14, build job → release job, draft Release (chosen)** | free for public repos, same host as the kit, least privilege per job, developer publishes | CI can't be exercised by agents |
| B. Single job with `contents: write` | simpler | build steps (third-party code) run with a write token and signing secrets together |
| C. Build locally and upload by hand | no CI | not reproducible; every release needs Rust on the developer's Mac |
| D. Homebrew cask/tap | familiar `brew install` | extra repo to maintain; still needs hosted binaries |

## Decision
`kanban-app-release.yml`: triggers `push: tags: [kanban-v*]` and `workflow_dispatch` (always a build-only dry run: no
release job, no signing); top-level `permissions: contents: read`; concurrency group per ref. Job **build**: checkout
with `persist-credentials: false`, tag/version check (tag pushes only, five version fields), tests
(plugin suites, installer, `test_app_python_lock.py`, `test_release_workflow.py`, guard, `cargo test --locked`),
`fetch-python.sh --arch both`, `package-release.sh`; a step writes `SIGNING=1` to `$GITHUB_ENV` when
`APPLE_CERTIFICATE` is set. Job **release** (tag pushes only, `needs: build`, `contents: write`): downloads artefacts,
`gh release create --draft --repo "$GITHUB_REPOSITORY"` with `GH_TOKEN: ${{ github.token }}`; it checks out nothing and
runs no project code. Actions pinned by commit SHA.

## Consequences
+ one-line install and browser download for users; write token never meets build code.
− unsigned builds need first-launch steps until the owner adds an Apple Developer ID.
- Nested Mach-O (`bin/python3*`, `libpython3.12.dylib`, `lib-dynload/*.so`) are signed with hardened runtime +
  `app/src-tauri/entitlements.plist` before the bundle when `SIGNING=1`, ad-hoc otherwise; `codesign --verify --deep
  --strict` gates both modes, followed by a smoke test of each executable arch's interpreter
  (`-E -c 'import ssl, sqlite3, ctypes, json'`); entitlement keys are added only when that smoke test fails. Whether
  Tauri 2 also signs `bundle.resources` is verification item V01.
- Tests that pin it: `tests/test_release_workflow.py` (`test_triggers_and_permissions`, `test_jobs_split`,
  `test_dispatch_is_dry_run`, `test_actions_pinned_by_sha`, `test_signing_flag_step`, `test_codesign_verify`,
  `test_tag_check_only_on_tags`, `test_concurrency`, `test_bundled_python_smoke`).
