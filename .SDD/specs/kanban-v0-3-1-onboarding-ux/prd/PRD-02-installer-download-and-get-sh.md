---
title: PRD-02 - Installer download mode, get.sh menu and pinned sums
status: done
updated: 2026-09-29
---

# PRD-02: Installer download mode, get.sh menu and pinned sums

| Field | Value |
|---|---|
| Requirements covered | KB31-FR-16, 17; KB31-NFR-05; KB31-NFR-01 (licences not copied, Q13) |
| ADRs | ADR-003 |
| Wave · Requires | 1 · — (contracts: asset names and hosts in architecture §2; bundle id read from `plugins/kanban/app/src-tauri/tauri.conf.json` `identifier`, read-only) |
| Parallelisable | Yes — wave 1 (with PRD-01, PRD-03) |

## Goal
On macOS the kit installer (and so `get.sh`) installs Kanban.app by downloading the matching release from the kit's own
GitHub repository, verified against pinned sums when the kit has them, and replaces an existing copy safely; building
from source stays available.

## Steps
1. Pinned-sums file, initially an empty object with a comment key explaining the format
   `{"kanban-v0.3.1": {"Kanban.app.zip": "<sha256>", "Kanban.dmg": "<sha256>"}}` — `plugins/kanban/app/releases.lock` (create)
2. Fake release fixture: a tiny valid `Kanban.app` tree (Info.plist with `CFBundleIdentifier` `dev.agentic-kit.kanban`,
   `CFBundleShortVersionString`), zipped, its `SHA256SUMS`, a releases-API JSON (published, draft, prerelease entries),
   and a loopback server helper that can answer 404/403/redirects — `tests/fixtures/fake_release/**` (create)
3. Tests first (see table); rewrite the v0.3 app tests that change: `test_kanban_app_dry_run`
   (`tests/test_install.py:137-150`, expects `cargo tauri build` by default → now the download plan; the build plan
   under `--from-source`), `test_kanban_app_not_in_all_or_update` (`:152-175` → becomes
   `test_app_in_all_and_yes` + `test_app_update_only_when_newer`), `test_kanban_app_missing_prereqs` (`:177-186` → only
   under `--from-source`) — `tests/test_install.py` (modify)
4. Component model: `kanban-app` leaves `EXPLICIT_ONLY` (`install.py:46`); offered in the menu on macOS, pre-selected
   when `kanban` is; `--yes` and `--all` include it on macOS; still in `NOT_FILES`; COMPONENTS text and the
   `APP_HINTS` comment updated (`install.py:35-56`) — `install.py` (modify)
5. Release source: repository from `AGENTIC_KIT_REPO`, else the kit clone's `origin` remote, else
   `https://github.com/yasinishyn/agentic-kit`; must parse as `https://github.com/<owner>/<repo>(.git)?` →
   base `https://github.com/<owner>/<repo>/releases`; `AGENTIC_KIT_RELEASES_URL` replaces the base only when its host
   is `127.0.0.1`, `::1` or `localhost` — `install.py`
6. Fetcher: stdlib `urllib` opener with a custom redirect handler — every hop `https` and host in {`github.com`,
   `api.github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`} (loopback `http` only
   under the test override), ≤ 5 hops, 30 s timeout, `User-Agent: agentic-kit-installer`; never follows other hosts —
   `install.py`
7. Release choice: tag `kanban-v<plugins/kanban/.claude-plugin/plugin.json version>`; `SHA256SUMS` 404 →
   `GET https://api.github.com/repos/<owner>/<repo>/releases?per_page=100`, drop drafts and prereleases, keep
   `kanban-v*` tags, pick the highest version, print a note — `install.py`
8. Verification: sums from `releases.lock[tag]` when present (and the release's `SHA256SUMS` must agree when fetched;
   disagreement → refuse), else the release's `SHA256SUMS` with the note "integrity only: sums not pinned in this kit";
   the zip is verified before unpacking — `install.py`
9. Install: stage in `~/Applications/.Kanban.app.staging-<pid>/` (same volume), `ditto -x -k`, require the top level to
   be exactly `Kanban.app` and its `CFBundleIdentifier` to equal `tauri.conf.json` `identifier`; detect a running
   Kanban.app or any process whose executable is under `~/Applications/Kanban.app/` (`ps -axo pid=,comm=`); with a
   TTY ask "quit Kanban and press Enter, or s to skip", without one defer with a note; then rename old →
   `.Kanban.app.old-<pid>`, move new in, remove old; staging removed on every exit path — `install.py`
10. `--update`: when the manifest lists `kanban-app`, compare the version derived from the chosen tag with the installed
    `CFBundleShortVersionString` (plistlib); download only when newer; never downloads a zip to compare — `install.py`
11. `--from-source`: the v0.3 cargo build (`install.py:306-330`) with the default config, i.e. no bundled Python
    (system `python3` fallback) unless the developer ran `fetch-python.sh` and passes the release config themselves;
    the output says so — `install.py`
12. `licences()` (`install.py:246-247`) unchanged; a test asserts no Python runtime licence file is copied into a
    project (Q13) — `tests/test_install.py`
13. Usage line and header comment — `install.sh` (usage), `get.sh` (comments only: the app comes with it on macOS;
    `python3` still required)

## Interfaces
| Input / output | Validation or consumer | Error states |
|---|---|---|
| `AGENTIC_KIT_REPO` / origin remote | must be `https://github.com/<owner>/<repo>` | other host or path → app skipped with "downloads need a github.com repository; use --from-source" |
| `AGENTIC_KIT_RELEASES_URL` | honoured only for loopback | non-loopback → ignored with a note |
| Release HTTP (assets, API) | allow-listed HTTPS hops ≤ 5 | foreign host / `http` hop / > 5 hops → refuse, nothing written; 404 on tag → fallback; API 403/429 → "GitHub API rate limit; try later or --from-source", app skipped, other components continue, exit code non-zero at the end |
| `plugins/kanban/app/releases.lock` | JSON object tag → {asset → 64-hex} | unparsable → refuse the download with the file name; entry mismatch → refuse |
| `Kanban.app.zip` | sha256, top level, bundle id | mismatch / extra top-level entries / wrong id → refuse, installed app untouched |
| Manifest | records `kanban-app` + installed version only after success | failure → not recorded |
| `--dry-run` | prints repository, tag, URLs, sums source and steps | no network, nothing written |

## Acceptance criteria
- [x] macOS menu: with `kanban` selected the app is pre-selected; `--yes` and `--all` include it; non-macOS prints a skip note (step 4, Q10, Q15)
- [x] A fork's `AGENTIC_KIT_REPO=https://github.com/alice/agentic-kit.git` downloads from `github.com/alice/agentic-kit/releases` (step 5)
- [x] Redirect to a foreign host, an `http` hop, or a 6th hop → refused, nothing written (step 6)
- [x] `AGENTIC_KIT_RELEASES_URL=https://evil.example/` is ignored; `http://127.0.0.1:<port>/` is used (step 5)
- [x] Missing tag → newest published, non-prerelease `kanban-v*` with a note; no published release → skip note suggesting `--from-source` (step 7)
- [x] Pinned entry present → pinned sums used; release `SHA256SUMS` disagreeing with it → refused; no entry → release sums used and the note printed (step 8, Q14)
- [x] Wrong top level or bundle id → refused; the existing `~/Applications/Kanban.app` is byte-identical afterwards (step 9)
- [x] Running app/daemon under the bundle: with `--yes` the update is deferred with a note and nothing is replaced (step 9)
- [x] `--update` with the same version → "up to date", no asset request made (fake server request log) (step 10)
- [x] `--from-source` prints the cargo build plan and says the build uses the system Python (step 11)
- [x] No file from the bundled runtime's licences appears under the project's `.claude/agentic-kit/LICENSES/` (step 12)
- [x] `--dry-run` makes no network request (step 4–11)

## Edge cases
| Case | Handling | Priority |
|---|---|---|
| No published release at all (API returns `[]` or only drafts) | skip the app with a note; exit status reflects the skipped component | must |
| Only prereleases match | ignored; treated as "no release" | must |
| GitHub API 403/429 (rate limit) | note with retry advice; other components continue | must |
| `SHA256SUMS` lacks `Kanban.app.zip` | refuse ("asset not in SHA256SUMS") | must |
| Interrupted download / disk full | temp files and staging removed; installed app untouched | must |
| `/Applications/Kanban.app` also exists | install to `~/Applications` only; note that `kpython` prefers `~/Applications` | should |
| `~/Applications` missing | created | must |
| Stale `.Kanban.app.old-*` / staging dirs from a crash | removed before staging | should |
| Installed app has no readable Info.plist | treated as older → reinstall under `--update` | should |
| Kit version has no release yet (developer ran `get.sh` on an unreleased version) | fallback latest with the note naming both versions | must |
| User declines to quit the running app | skip with the note "run `install.py --only kanban-app` after quitting" | must |

## Out of scope
- Building or publishing releases (PRD-04); filling `releases.lock` entries (developer, documented in PRD-07).
- Mounting the `.dmg` (ADR-003 option B); Linux/Windows apps.
- Changing `get.sh` behaviour (comments only).

## Owned files (only these may change)
- `install.py`
- `install.sh` (usage line)
- `get.sh` (comments only)
- `plugins/kanban/app/releases.lock`
- `tests/test_install.py`
- `tests/fixtures/fake_release/**`

## Forbidden files
- Other PRDs' owned files (notably `tauri.conf.json`, read only); `.claude/**`; `.SDD/templates/**`

## Tests to write first (TDD)
| Test | Asserts |
|---|---|
| `test_app_download_happy_path` | loopback fake release → app installed, version recorded |
| `test_app_pinned_sums_preferred` / `test_app_pinned_mismatch_refused` | Q14 behaviour |
| `test_app_bad_sum_refused` | nothing replaced, non-zero exit |
| `test_app_foreign_host_refused` / `test_app_https_downgrade_refused` / `test_app_hop_limit` | redirect handler |
| `test_app_override_loopback_only` | non-loopback override ignored |
| `test_app_base_url_from_repo` | fork URL derivation; non-github repo → skip note |
| `test_app_fallback_latest_ignores_prereleases` / `test_app_no_release` / `test_app_api_rate_limited` | release choice |
| `test_app_wrong_bundle_refused` | top level / bundle id |
| `test_app_running_deferred` | fake process list → deferred, nothing replaced |
| `test_app_in_all_and_yes` / `test_app_menu_default_on_macos` | selection (replaces `:152-175`) |
| `test_app_update_only_when_newer` | tag-derived version; no asset request when equal |
| `test_app_from_source_plan` | cargo plan, system-Python note (replaces `:137-150`, `:177-186`) |
| `test_python_licences_not_copied` | Q13 |

## Gates
```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py'
python3 -m unittest discover -s tests -p 'test_*.py'
bash .claude/hooks/test_guard_bash.sh
claude plugin validate plugins/kanban && claude plugin validate .
```

## Deliverable
Owned files changed; the orchestrator stages them. Report: files, tests (verbatim counts), suggested commit message
`PRD-02: <summary> (KB31-FR-16, 17)`, open issues. Blocked → `status: blocked` + blocker type and one line.
