# ADR-003: Installer downloads the matching app release

| Field | Value |
|---|---|
| Status | Accepted — Decided by kit owner, 2026-09-29 (Q10, Q11, Q14, Q15); amends v0.3 PRD-08 'never downloads' |
| Date | 2026-09-29 (rev 2) |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-1-onboarding-ux |

## Context
The `get.sh` one-liner should install the app without a local toolchain; v0.3's installer promised it never downloads
(`install.py:56` `APP_HINTS`, `:306-330` `build_kanban_app`) and kept the app out of `--all`/`--update`
(`install.py:46` `EXPLICIT_ONLY`, pinned by `tests/test_install.py:152-175`).

## Options considered
| Option | For | Against |
|---|---|---|
| **A. Download `Kanban.app.zip` of the matching release, verify sha256 (pinned first), `ditto` into ~/Applications (chosen)** | no toolchain; no quarantine flag (not a browser download); version matches the plugin | the installer now fetches a binary |
| B. Mount the `.dmg` with `hdiutil` | same artefact as browser users | mount/unmount handling, prompts |
| C. Keep build-from-source only | no download | needs Rust; the owner wants a one-liner |

## Decision
Base `https://github.com/<owner>/<repo>/releases` from `AGENTIC_KIT_REPO` (Q15); tag `kanban-v<plugin.json version>`,
fallback newest published non-prerelease `kanban-v*` via the GitHub API. `urllib` with a custom redirect handler: every
hop HTTPS to `github.com`, `api.github.com`, `objects.githubusercontent.com` or `release-assets.githubusercontent.com`,
≤ 5 hops; `AGENTIC_KIT_RELEASES_URL` only for loopback. Sums: `plugins/kanban/app/releases.lock[tag]` when present
(mismatch → refuse), else the release's `SHA256SUMS` with a note (Q14). Stage in `~/Applications`, require exactly
`Kanban.app` with `CFBundleIdentifier` = `tauri.conf.json` identifier (`dev.agentic-kit.kanban`, `:5`), ask to quit or
defer when the app or its daemon runs, then swap atomically. In the menu, `--yes` and `--all` on macOS (Q10, Q15);
`--update` compares the tag's version with the installed one; `--from-source` keeps the cargo build.

## Consequences
+ zero-toolchain install; authenticity for every release whose sums the developer pinned.
− integrity only for unpinned releases (said in the output); the kit repo must be on github.com for the download.
- Tests that pin it (`tests/test_install.py`): `test_app_download_happy_path`, `test_app_pinned_sums_preferred`,
  `test_app_pinned_mismatch_refused`, `test_app_bad_sum_refused`, `test_app_foreign_host_refused`,
  `test_app_https_downgrade_refused`, `test_app_hop_limit`, `test_app_override_loopback_only`,
  `test_app_fallback_latest_ignores_prereleases`, `test_app_no_release`, `test_app_api_rate_limited`,
  `test_app_running_deferred`, `test_app_wrong_bundle_refused`, `test_app_in_all_and_yes`,
  `test_app_update_only_when_newer`, `test_app_base_url_from_repo`.
