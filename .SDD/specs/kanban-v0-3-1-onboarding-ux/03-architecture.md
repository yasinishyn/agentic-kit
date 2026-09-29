# Kanban v0.3.1 — Architecture

| Field | Value |
|---|---|
| Spec | `kanban-v0-3-1-onboarding-ux` · [01-discovery.md](01-discovery.md) (Agreed 2026-09-29; FR-13, 16, 17 and NFR-01 amended in Architect by Q12–Q15) |
| Tier | Full (release pipeline, bundled runtime, installer download) |
| Builds on | v0.3 (`kanban-v0-3-agent-board`, architecture §3.1–3.3 plug-in contracts, §8 security) |
| Status | Draft rev 3 — waiting for the user's approval ("execute") |

Paths are relative to `plugins/kanban/`; repo-root paths start with `/` (e.g. `/install.py`, `/tests/test_install.py`).

## 0. Codebase context
| Area | Existing component (path) | Pattern to match / constraint / likely conflict |
|---|---|---|
| Python resolution in the app | `app/src-tauri/src/python.rs:4,14-25` (`FALLBACKS`, `candidates`), `app/src-tauri/src/shell.rs:84-99` (`find_python` + error strings), `:101-105` (`daemon_command`), `:423` (`resource_dir`) | ordered candidates, `--version` ≥ 3.9; bundled interpreter becomes candidate 0 (PRD-01) |
| Plugin Python | `.mcp.json` (`"command": "python3"`), `hooks/hooks.json` (`python3 …/hook.py`) | both call the new `scripts/kpython` launcher (Q12, PRD-03) |
| App resources | `app/src-tauri/tauri.conf.json:28-35` (`bundle.resources` → `Resources/kanban/{scripts,ui}`), `:5` identifier `dev.agentic-kit.kanban` | default config stays free of python resources; `tauri.release.conf.json` adds `Resources/python/<arch>/` (PRD-01) |
| App IPC scope | `app/src-tauri/build.rs:4-8` (`AppManifest` commands), `src/scope.rs:10-20` (runtime capability), `:23-27` (`terminal_allowed`), `src/shell.rs:216-226` (`grant`), `:304-312` (`allowed_board`), `:418` (`invoke_handler`) | no static `capabilities/` directory exists; `pick_folder` joins the same runtime capability (PRD-06) |
| Installer | `/install.py:35-47` (`COMPONENTS`, `EXPLICIT_ONLY`, `NOT_FILES`), `:56` (`APP_HINTS` "never downloads"), `:239-247` (`kanban_app`, `licences`), `:306-330` (`build_kanban_app`); `/tests/test_install.py:137-186` | `kanban-app` leaves `EXPLICIT_ONLY`; download is its default mode on macOS (PRD-02) |
| Bootstrap | `/get.sh:10,15` (`AGENTIC_KIT_REPO`), `:23-24` (python3 required) | unchanged interface; `python3` still required by `/get.sh` (Q12) |
| Sessions | `scripts/kanban_db.py:28` (`SCHEMA_VERSION = 1`), `:51-54` (sessions table), `:159-170` (`migrate`), `:273-281` (`register_session`), `:494-506` (`Settings`); `scripts/daemon.py:76-78` (`UI_ONLY`), `:199-226` (`Bus.subscribe/unsubscribe/session_ids`), `:361-370` (`project_root` marker rule), `:535-542` (`live_sessions`), `:598-611` (core routes), `:620-645` (`register_project`, `h_add_project`, `h_session`); `scripts/server.py:316` (`channel_from_args`), `:335` (`parent_args`), `:531-550` (registration) | additive `sessions.origin` column without a `user_version` bump (ADR-004, PRD-03) |
| Board UI | `ui/index.html`, `ui/app.js:324-338` (`window.kanban`, `addDrawerPanel(id, title, render)` at `:329`), `ui/board.js`, `ui/board.css`, `ui/modules/*` | drawer panels become tabs; plug-in API gains an optional `{tab}` and `addProjectPanel` (PRD-05) |
| Terminal | `app/src-tauri/src/pty.rs:10` (`CLAUDE_CHANNELS`), `ui/modules/terminal.js:161-181` (per-project session shown in the ticket drawer) | project-level Connect Claude reuses the same per-project session (PRD-06) |
| Versions | `.claude-plugin/plugin.json:4`, `/.claude-plugin/marketplace.json:9`, `scripts/daemon.py:61`, `app/src-tauri/tauri.conf.json:4`, `app/src-tauri/Cargo.toml:3` (all `0.3.0`) | bumped together by PRD-07; CI tag check compares all five (PRD-04) |
| CI | none (`/.github/` does not exist) | new workflow; the developer pushes tags, agents never do |

## 1. Bounded context
Unchanged from v0.3 (SDD work tracking; markdown is the source of truth). v0.3.1 adds a **distribution** concern (how
the app and its runtime reach a user's Mac) and an **onboarding** concern (how a user gets from an empty app to a
connected project). Neither changes stage, approval, hand-off or runner rules.

## 2. Distribution
```
developer: git tag kanban-v0.3.1 && git push --tags
   └─► GitHub Actions kanban-app-release.yml (macos-14, actions pinned by SHA, concurrency group per ref)
        job build   (permissions: contents: read; checkout persist-credentials: false; signing secrets only here)
          1. tag/version check (tag pushes only): tag == kanban-v<v> and v equal in all five version files
          2. python3 -m unittest … (plugin suites, tests/test_install.py, tests/test_app_python_lock.py,
             tests/test_release_workflow.py) · guard test · cargo test --locked
          3. app/scripts/fetch-python.sh --arch both  ← app/python.lock (url + sha256 per arch)
             → src-tauri/resources/python/{arm64,x86_64}  (pruned, licence files kept, compileall unchecked-hash)
          4. app/scripts/package-release.sh: sign nested Mach-O (identity when SIGNING=1, else ad-hoc) →
             cargo tauri build --target universal-apple-darwin --bundles app,dmg --config tauri.release.conf.json →
             codesign --verify --deep --strict → bundled python -E smoke test (ssl, sqlite3, ctypes, json) per
             executable arch → ditto → Kanban.app.zip · shasum -a 256 → SHA256SUMS
          5. upload artefacts (dist/)
        job release (tag pushes only; needs: build; permissions: contents: write; no checkout of untrusted code)
          download artefacts → GH_TOKEN=github.token gh release create <tag> --repo $GITHUB_REPOSITORY --draft
          (Kanban.dmg, Kanban.app.zip, SHA256SUMS)
        workflow_dispatch = build-only dry run: no release job, no signing
developer: reviews + publishes the draft → commits SHA256SUMS into app/releases.lock (Q14)
user: curl … get.sh | bash -s -- <project>
   └─► install.py menu: [x] kanban  [x] Kanban desktop app (download)   (macOS; also --yes and --all)
        base = https://github.com/<owner>/<repo>/releases  from AGENTIC_KIT_REPO (Q15)
        tag  = "kanban-v" + .claude-plugin/plugin.json version   (fallback: newest published, non-prerelease kanban-v*)
          → SHA256SUMS + Kanban.app.zip (every hop HTTPS + allow-listed host)
          → verify against app/releases.lock[tag] if present (refuse on mismatch), else release SHA256SUMS (+ note)
          → stage in ~/Applications/.Kanban.app.staging-<pid>/ → top level must be exactly Kanban.app with
            CFBundleIdentifier == tauri.conf.json identifier → running app/daemon? ask to quit or defer → atomic swap
```
- **Python runtime (ADR-001):** python-build-standalone `install_only_stripped`, CPython 3.12, one per arch, placed at
  `Resources/python/{arm64,x86_64}/`. Pruned: `test`/`tests`, `idlelib`, `tkinter`, `turtledemo`, `ensurepip`,
  `lib2to3`, `pydoc_data`, `lib/tcl8.6`, `lib/tk8.6`, `libtcl*`/`libtk*`, `include/`, `lib/python3.12/config-*`, static
  `libpython*.a`, shipped `__pycache__`. **Never pruned:** the licence files (list in `python.lock` `licence_files`).
  After pruning, `compileall --invalidation-mode unchecked-hash` precompiles the stdlib (bytecode is arch-independent;
  the host-arch interpreter compiles both trees). Ceiling **≤ 45 MB unpacked per arch** (adjust only with an ADR-001
  note). Python resources appear only in `app/src-tauri/tauri.release.conf.json` (merged with `--config`), so
  `cargo test` / `cargo tauri dev` / `--from-source` work without fetched Python (V02).
- **Isolated bundled interpreter, no bytecode writes into the bundle:** `shell.rs` runs the daemon on the bundled
  interpreter with `-E -B` (ignores `PYTHONHOME`/`PYTHONPATH`; `-B` replaces `PYTHONDONTWRITEBYTECODE`, which `-E`
  drops); `kpython` does the same; system interpreters get `PYTHONDONTWRITEBYTECODE=1`.
- **Bundled interpreter path (contract shared by `python.rs` and `kpython`):**
  `<Kanban.app>/Contents/Resources/python/<arm64|x86_64>/bin/python3` (`aarch64`→`arm64`; `uname -m` gives the
  running arch).
- **Plugin launcher (Q12):** `scripts/kpython` (POSIX `sh`): candidates `~/Applications/Kanban.app/…`,
  `/Applications/Kanban.app/…` (macOS only), then `python3` on PATH (≥ 3.9; `/usr/bin/python3` only when
  `xcode-select -p` succeeds, because the stub opens the CLT installer); each bundled candidate is run-checked
  (`-E -B -c 'import sys'`) and skipped on failure; a bundled interpreter is `exec`ed with `-E -B`, a system one with
  `PYTHONDONTWRITEBYTECODE=1`; missing → one stderr line + exit 127 (`--quiet-missing`, used by hooks, exits 0
  silently); `--probe` prints `bundled|system <path>` or `missing`. `.mcp.json` and `hooks/hooks.json` run
  `sh "${CLAUDE_PLUGIN_ROOT}/scripts/kpython" …`. `server.py` reports the interpreter it runs on (`sys.executable` →
  `bundled`/`system`/`unknown`) at registration, stored in `sessions.python`; the welcome's `plugin_python` uses the
  live sessions' reports, and only before any session exists a daemon-side hint that mirrors `kpython`'s order (pinned
  by a parity test against one fake `$HOME`).
- **Licences (Q13):** the runtime's licence files stay inside `Kanban.app` (`Resources/python/<arch>/…`), are listed in
  `/LICENSES/NOTICE.md`, and never enter the repo's `/LICENSES/` directory — so `/install.py`'s `licences()`
  (`/install.py:246-247`) never copies them into projects.
- **Signing-ready (ADR-002):** a step maps "APPLE_CERTIFICATE is set" to `SIGNING=1` in `$GITHUB_ENV` (secrets cannot be
  used in step `if:`); nested Mach-O (`bin/python3*`, `libpython3.12.dylib`, `lib-dynload/*.so`) are signed with
  hardened runtime + `app/src-tauri/entitlements.plist` before the bundle when `SIGNING=1`, ad-hoc otherwise; Tauri
  signs/notarises the bundle with the `APPLE_*` env only when `SIGNING=1`; unsigned builds are ad-hoc signed and the
  release notes say "unsigned". `codesign --verify --deep --strict` runs in both modes. Whether Tauri 2 signs
  `bundle.resources` itself: V01.
- **Installer download (ADR-003):** stdlib `urllib` with a custom redirect handler: every hop must be HTTPS to one of
  **`github.com`, `api.github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`**, at most
  5 hops; `AGENTIC_KIT_RELEASES_URL` is honoured only when it points at loopback (`127.0.0.1`, `::1`, `localhost`; for
  tests). Base URL derived from `AGENTIC_KIT_REPO` (else the kit clone's `origin` remote, else
  `github.com/yasinishyn/agentic-kit`); a non-github.com repo → the app is skipped with a note (`--from-source`
  remains). Fallback listing: `GET api.github.com/repos/<owner>/<repo>/releases?per_page=100`, drafts and prereleases
  ignored; 403/429 (rate limit) → skip with a note, other components unaffected. `--update` compares the version
  derived from the chosen tag with the installed `CFBundleShortVersionString` (no zip download to compare).
  `--from-source` keeps the v0.3 cargo build with the default config (no bundled Python unless the developer ran
  `fetch-python.sh` and passes the release config). Non-macOS: skipped with a note.

## 3. Onboarding
- **Welcome:** shown when no project is registered (step 1 state from `GET /api/projects`) and, for the selected
  project, until all steps are done or it is dismissed. Steps: *Add a project* (≥ 1 project); *Connect Claude* — done
  when the selected project has a **live `interactive` session with `channel = 1`**; *Move your first card* — done after
  the first human move in that project (settings `onboarding.first_move.<project>`). State from
  `GET /api/projects/{p}/onboarding` → `{connected, first_move, dismissed, plugin_python}`; `plugin_python = missing`
  shows the fix (move Kanban.app to Applications or install python3) under the Connect step. Dismissed:
  `POST /api/projects/{p}/onboarding {dismissed: true}` (UI_ONLY) stores `onboarding.dismissed.<project>` in the daemon
  settings (Q16: shared by the app window and browser tabs). Reachable again from the help menu.
- **Add project (ADR-006):** `POST /api/projects/add {path, create_specs?, dry_run?}` (UI_ONLY). `~` expands to the
  daemon user's home; the path is resolved; the **marker rule (`.SDD`, `.git`, `.claude`) is checked on the folder
  as-is before anything is created**. `dry_run: true` resolves and reports `{resolved, name, markers, has_specs,
  already_registered, is_home, would_create}` and changes nothing; the UI always shows this preview (resolved path,
  a warning when `is_home` — `~` passes the marker through `~/.claude`) and only then sends the real call.
  `create_specs: true` creates `.SDD/specs/` only (Q04). Adding clears a removal tombstone.
- **Remove project:** `DELETE /api/projects/{p}` (UI_ONLY) → 409 while the project has live runs; otherwise deletes the
  project row and its session rows (handoff/run history stays; re-adding the same folder yields the same id), writes a
  tombstone setting `project.removed.<id>`, closes that project's event subscriptions and publishes
  `project.removed {id}` to every board. While tombstoned, `POST /api/sessions` for that folder answers **410**;
  `server.py` then keeps the MCP tools working on the markdown, starts **no embedded UI**, and prints one notice with
  how to re-add (app → Add project, or `daemon.py --open <folder>`); `POST /api/projects` (`daemon.py --open`) and the UI add route
  re-add it and clear the tombstone.
- **Connection panel (ADR-004):** `GET /api/projects/{p}/sessions` (read) → `{sessions: [{id, kind, origin, channel,
  last_seen, run_id}]}` for live sessions. `origin` ∈ `cli`, `cli-print`, `code-tab`, `headless`, `unknown`:
  `server.py` classifies the parent `claude` argv (both `--input-format stream-json` and `--output-format stream-json`
  → `code-tab`; else `-p`/`--print` → `cli-print`; else `cli`; unreadable argv → `unknown`); the daemon overrides with
  `headless` for derived headless runs and stores it in the additive `sessions.origin` column. Labels only. **Live
  updates:** the daemon publishes `session.changed {session, live}` to the project on registration, subscription open
  and subscription close; the panel refetches on the event and on stream reconnect.
- **Connect Claude:** project-level. App: `window.kanban.connectClaude(projectId)` (terminal.js) starts or reveals the
  project terminal with the `claude-channels` preset. Browser (no `connectClaude`): the command with a Copy button.
  Explains that Code-tab sessions get no board moves, and that "Copy prompt"/"Run headless" on queued cards is the
  way for them.

## 4. UI structure and design (ADR-005)
- **Tokens** in `ui/board.css` `:root` (spacing 4/8/12/16/24, type 12/13/15/18, radius, neutral palette, accent,
  status colours) with a dark variant; all components use tokens; contrast ≥ 4.5:1.
- **Header:** title · project switcher · Add project · New ticket (v0.3 B14 form, restyled) · connection summary chip
  (e.g. "2 sessions · channels on") · help. The developer-internal hint text goes to the help menu.
- **Board:** 6 working columns share the width; Done and E2E render as 44 px rails with name and count, expand on
  click/Enter (state in `localStorage`, wrapped in try/catch). Fits 1280×800 without hiding a column.
- **Cards:** title, badges (approval, live indicator), sub-task progress (`done/total` of PRD/task files, not the
  stage checklist); ← / → and actions in a keyboard-reachable "⋯" menu; drag unchanged.
- **Ticket panel:** tabs Overview · Spec · Runs · Terminal · Git.

### 4.1 Plug-in contract between PRD-05 (provider) and PRD-06 (consumer) — pinned
| API (on `window.kanban`) | Provided by | Contract |
|---|---|---|
| `addDrawerPanel(id, title, render, {tab} = {})` | PRD-05 (`ui/app.js`) | `tab` ∈ `overview` \| `spec` \| `runs` \| `terminal` \| `git`; missing or unknown → `overview` (v0.3 modules keep working); re-registering an `id` replaces it; `render(box, ticket)` as in v0.3 |
| `addProjectPanel(id, title, render)` | PRD-05 (`ui/app.js`) | `render(box, project)` is called when the project panel area renders (project switch, board reload, and immediately when a panel is added); same `id` replaces |
| `connectClaude(projectId) → Promise<"started" \| "revealed">` | PRD-06 (`ui/modules/terminal.js`) | set on `window.kanban` **before** terminal.js calls `addProjectPanel("terminal", …)`; rejects with a user-readable message (not the current project, command refused by scope, pty error); only in the app |
| Feature detection | PRD-05 | the Connection panel and the welcome use `typeof window.kanban.connectClaude === "function"` at render time; otherwise the browser copy-command path. Because `addProjectPanel` re-renders project panels, detection is correct regardless of module load order |
| Project terminal host | PRD-06 | the project panel "terminal" and the drawer tab "terminal" re-attach **the same per-project session host node** (v0.3 `session(project)`); one pty per project; the node lives in whichever view rendered last |
| Tokens / classes | PRD-05 | `board.css` exposes the tokens and the classes `term-bar`, `muted`, `panel`; terminal.js uses them instead of literal colours or spacing |

Dependency direction: **PRD-06 Requires PRD-05** (it consumes `addProjectPanel`, `{tab}` and the tokens); PRD-05 never
imports terminal.js behaviour, only feature-detects it. PRD-06 therefore runs in wave 3.

## 5. Security (delta to v0.3 §8)
| Threat | Surface | Control | Test |
|---|---|---|---|
| Malicious or tampered release asset | installer download | every hop HTTPS to the four allow-listed hosts (custom redirect handler, ≤ 5 hops, no downgrade), fixed asset names, pinned sums from `app/releases.lock` preferred (Q14; mismatch → refuse), else release `SHA256SUMS`; staged unpack; top level exactly `Kanban.app` with the expected bundle id; never executes the download | `/tests/test_install.py` with a loopback fake release (bad sum, pinned mismatch, foreign host, HTTP downgrade hop, too many hops, API fallback, API 403) |
| Test override abused for a foreign base URL | `AGENTIC_KIT_RELEASES_URL` | honoured only for loopback hosts; otherwise ignored with a note | `/tests/test_install.py` |
| Replacing a running app | installer swap | detect a running `Kanban.app` or daemon whose executable is under the bundle; ask to quit (TTY) or defer (`--yes`); never kills processes; staging on the same volume | `/tests/test_install.py` (fake process list) |
| Supply chain in CI | workflow | actions pinned by SHA, `python.lock` sha256 checked before use, `cargo --locked`, build job `contents: read` + `persist-credentials: false`, release job `contents: write` without checkout of untrusted code, no `pull_request*` triggers, dispatch = dry run without signing, concurrency group | `/tests/test_release_workflow.py` |
| Nested unsigned code | `Resources/python` Mach-O | signed before the bundle (identity or ad-hoc), `codesign --verify --deep --strict` gate | CI step + `/tests/test_release_workflow.py` |
| Bytecode written into the bundle; hostile `PYTHONHOME`/`PYTHONPATH` | bundled interpreter | precompiled stdlib; `-E -B` for the bundled interpreter (shell.rs, kpython); run-check before choosing it | cargo `daemon_command_isolated_bundled`, `test_kpython.py` |
| Folder picker path from the page | Tauri `pick_folder` | granted only through the runtime capability (`allow-pick-folder` only, no `dialog:*` permissions); the command re-checks window label + origin like `terminal_allowed`; the path still goes through the daemon's marker rule and the dry-run preview | cargo test (scope) + daemon test |
| Registering arbitrary paths via the UI | `POST /api/projects/add` | UI token only (UI_ONLY), marker rule before creation, dry-run preview, `is_home` warning, `create_specs` creates only `.SDD/specs` | `test_daemon.py`, `fixtures/abuse_probe.py` |
| Removal bypassed by a live session | `DELETE` + `POST /api/sessions` | tombstone → 410; subscriptions closed; `project.removed` | `test_daemon.py` |
| Bundled interpreter hijack | `python.rs`, `kpython` | bundled path inside the app bundle is preferred; system candidates unchanged | cargo test, `test_kpython.py` |

## 6. Execution map (PRDs)
| PRD | Scope | Wave | Requires | Owns (files and shared artefacts) | Parallelisable |
|---|---|---|---|---|---|
| [PRD-01](prd/PRD-01-bundled-python-runtime.md) | Bundled Python runtime, release-only config, app resolution + daemon env | 1 | — | `app/python.lock`, `app/scripts/fetch-python.sh`, `app/src-tauri/tauri.release.conf.json` (create), `app/src-tauri/src/python.rs`, `app/src-tauri/src/shell.rs` (python + daemon env parts), `app/.gitignore`, `/tests/test_app_python_lock.py`, `/tests/fixtures/fake_python/**` | Yes |
| [PRD-02](prd/PRD-02-installer-download-and-get-sh.md) | Installer download mode, `/get.sh` menu, pinned sums | 1 | — (contracts: asset names §2, bundle id `tauri.conf.json:5` read-only) | `/install.py`, `/install.sh` (usage), `/get.sh` (comments only), `app/releases.lock` (create), `/tests/test_install.py`, `/tests/fixtures/fake_release/**` | Yes |
| [PRD-03](prd/PRD-03-sessions-origin-and-project-routes.md) | Plugin launcher, session origin, sessions/onboarding/add/remove routes, `session.changed`, `project.removed` | 1 | — | `scripts/kpython` (create), `.mcp.json`, `hooks/hooks.json`, `scripts/kanban_db.py`, `scripts/daemon.py` (except `VERSION`), `scripts/server.py`, `tests/test_db.py`, `tests/test_daemon.py`, `tests/test_daemon_auth.py`, `tests/test_mcp_channel.py`, `tests/test_kpython.py` (create), `tests/test_abuse.py`, `tests/fixtures/abuse_probe.py` | Yes |
| [PRD-04](prd/PRD-04-release-workflow.md) | Release workflow, packaging + nested signing, entitlements | 2 | PRD-01 | `/.github/workflows/kanban-app-release.yml`, `app/scripts/package-release.sh`, `app/src-tauri/entitlements.plist`, `app/src-tauri/tauri.release.conf.json` (sequenced after PRD-01: adds `bundle.macOS.entitlements`), `/tests/test_release_workflow.py` | Yes |
| [PRD-05](prd/PRD-05-ui-design-and-onboarding.md) | Tokens, header, rails, cards, tabbed panel, welcome, Add project, connection panel, plug-in API | 2 | PRD-03 | `ui/index.html`, `ui/app.js`, `ui/board.js`, `ui/board.css`, `ui/modules/runs.js`, `ui/modules/runs.css`, `ui/modules/transcript.js`, `ui/modules/specs.js`, `ui/modules/specs.css`, `tests/test_ui_contract.py` (create) | Yes |
| [PRD-06](prd/PRD-06-app-folder-picker-and-connect.md) | Tauri folder picker, project-level Connect Claude | 3 | PRD-01, PRD-05 | `app/src-tauri/Cargo.toml` (deps), `app/src-tauri/Cargo.lock`, `app/src-tauri/build.rs`, `app/src-tauri/src/scope.rs`, `app/src-tauri/src/shell.rs` (sequenced after PRD-01: command + plugin init), `app/src-tauri/permissions/autogenerated/**`, `ui/modules/terminal.js`, `tests/test_terminal_contract.py` (create) | No (alone in wave 3) |
| [PRD-07](prd/PRD-07-readme-quick-start-and-release-docs.md) | README quick start, release docs, NOTICE, version 0.3.1 in all five places | 4 | PRD-01…06 | `README.md`, `/README.md`, `/LICENSES/NOTICE.md`, `.claude-plugin/plugin.json`, `/.claude-plugin/marketplace.json`, `scripts/daemon.py` (`VERSION` line only, after PRD-03), `app/src-tauri/tauri.conf.json` (`version` only), `app/src-tauri/Cargo.toml` (`version` only, after PRD-06), `app/src-tauri/Cargo.lock` (root package version, after PRD-06), `docs/img/**`, `/tests/test_versions_and_docs.py` (create) | No |

Plugin test paths (`tests/test_db.py` …, `tests/fixtures/abuse_probe.py`) are under `plugins/kanban/tests/`.

Hotspots and sequencing:
- `app/src-tauri/src/shell.rs`: PRD-01 (wave 1: `find_python`, error strings, `resource_dir`, `daemon_command` env) →
  PRD-06 (wave 3: `pick_folder`, `invoke_handler`, dialog plugin init).
- `app/src-tauri/tauri.release.conf.json`: PRD-01 creates (wave 1) → PRD-04 adds entitlements (wave 2).
- `app/src-tauri/Cargo.toml` / `Cargo.lock`: PRD-06 (deps, wave 3) → PRD-07 (version, wave 4).
- `scripts/daemon.py`: PRD-03 (wave 1) → PRD-07 (`VERSION`, wave 4). `tauri.conf.json`: PRD-07 only (version);
  no other PRD edits it.
- Schema: additive `sessions.origin` in PRD-03 only; `user_version` unchanged.
- Within each wave the Owns sets are disjoint: wave 1 {PRD-01, 02, 03}, wave 2 {PRD-04, 05}, wave 3 {PRD-06},
  wave 4 {PRD-07}. Requires edges point only to earlier waves (acyclic).

**Map review (architect agent):** no blocking conflicts (architect re-review 2026-09-29)

## 7. Test strategy
| Layer | Tool | Files |
|---|---|---|
| Python lock / fetch script | unittest (lock format, sha256 refusal, prune list incl. licence files kept, size ceiling, `.pyc` present) with a local fixture archive | `/tests/test_app_python_lock.py`, `/tests/fixtures/fake_python/**` |
| Plugin launcher | unittest over `sh scripts/kpython` with a fake `$HOME`/PATH (bundled first, system fallback, missing, `--quiet-missing`, `--probe`, env) + parity with the daemon's `plugin_python` | `tests/test_kpython.py` |
| Installer download | unittest + loopback fake release (good, bad sum, pinned sums, pinned mismatch, foreign host, downgrade, hop limit, missing asset, fallback latest, prereleases ignored, no release, API 403, running app, wrong bundle id, non-macOS skip, `--update`, `--all`) | `/tests/test_install.py` |
| Daemon routes, schema | unittest (additive column idempotent, v0.3.0-style inserts still work, origin labels, sessions route, `session.changed`, add dry-run/real, delete + tombstone + `project.removed`, onboarding, UI_ONLY) | plugin tests + `fixtures/abuse_probe.py` |
| Release workflow | unittest structure checks (pinned SHAs, jobs/permissions, triggers, dry-run dispatch, signing flag step, concurrency, tag check on tags only, codesign verify) + a local run of `package-release.sh --host-only` | `/tests/test_release_workflow.py` |
| UI | contract tests on served files (no inline script, tokens only in PRD-05 files, tabs API, header controls, queued-card explanation) + Demo in the built-in browser and the app | `tests/test_ui_contract.py`, `06-demo.md` |
| Tauri | cargo test (python order incl. bundled, daemon env, pick_folder scope, capability permission list) + app build | `app/src-tauri` |
| Versions + docs | unittest (five versions equal, README links/images resolve, NOTICE lists the runtime licences) | `/tests/test_versions_and_docs.py` |
| Release end to end | a `workflow_dispatch` dry run, then the first tag (developer-triggered) | E2E report |

## 8. Risks (residual)
| Risk | Mitigation / owner |
|---|---|
| Unsigned app (Gatekeeper) for browser downloads | installer path avoids quarantine; README steps incl. macOS 15+ "Open Anyway"; signing when the owner adds secrets |
| Two bundled runtimes (≤ 45 MB each unpacked) | accepted with "universal" (Q08); per-arch builds are a later option |
| python-build-standalone is a third-party binary | pinned + sha256 + licences shipped; one place (`python.lock`) to bump |
| A v0.3.0 daemon opens a v0.3.1 DB | additive column, no `user_version` bump (ADR-004); v0.3.0 ignores the column |
| Kanban.app run from outside Applications on a Mac without python3 | welcome shows `plugin_python: missing` with the fix; README troubleshooting |
| CI can't be run by agents (no push) | structure tests + local packaging run; the developer does the dispatch dry run and the first tag (E2E) |

## Architect review 1 — disposition
| # | Finding | Resolution | PRD |
|---|---|---|---|
| 1 | PRD-01 must own `shell.rs` python parts; PRD-06 edits after | PRD-01 owns `shell.rs` (wave 1: `find_python` `:84-99`, errors, `resource_dir` `:423`, `daemon_command` `:101-105`); PRD-06 edits it in wave 3, Requires PRD-01 | PRD-01, PRD-06 |
| 2 | PRD-06 owned a nonexistent `capabilities/**` | replaced by `build.rs` (`AppManifest`), `scope.rs`, `permissions/autogenerated/**`; AC: only `allow-pick-folder`, no `dialog:*`; `pick_folder` re-checks window + origin | PRD-06 |
| 3 | PRD-05↔PRD-06 contract not pinned; cycle risk | §4.1 pins signatures, tab ids, `connectClaude`, feature detection, shared terminal host; PRD-06 Requires PRD-05 → wave 3 | PRD-05, PRD-06 |
| 4 | Version bump in all places | PRD-07 owns the five version fields (+ `Cargo.lock`), sequenced after their owners; CI compares all; test `/tests/test_versions_and_docs.py` | PRD-07, PRD-04 |
| 5 | One installer host allow-list, redirects, override | four hosts; custom redirect handler (HTTPS, allow-listed, ≤ 5 hops); override loopback-only; downgrade + API fallback tests; §2 and §5 aligned | PRD-02 |
| 6 | Replacing a running app | detect app/daemon under the bundle, ask or defer; staging in `~/Applications`; top level + bundle id check (from `tauri.conf.json` identifier) | PRD-02 |
| 7 | No runtime `.pyc` into the bundle | compileall unchecked-hash at fetch; `PYTHONDONTWRITEBYTECODE=1` in `shell.rs` and `kpython` | PRD-01, PRD-03 |
| 8 | Nested Mach-O signing | nested signing (identity or ad-hoc) before the bundle, entitlements file, `codesign --verify --deep --strict`; ADR-002 Consequences; V01 | PRD-04 |
| 9 | Licences | Q13: shipped in the bundle, never pruned (test), listed in NOTICE, not copied into projects | PRD-01, PRD-02, PRD-07 |
| 10 | Schema skew | additive idempotent `sessions.origin` via `PRAGMA table_info`, no `user_version` bump; ADR-004 Accepted; README troubleshooting row | PRD-03, PRD-07 |
| 11 | CI structure | dispatch = dry run; `SIGNING` env step; build/release job split with least privilege; concurrency; tag check on tags only | PRD-04 |
| 12 | Gates + release-only config | CI and gates run `test_app_python_lock.py` and `test_release_workflow.py`; python resources only in `tauri.release.conf.json`; V02 | PRD-01, PRD-04 |
| 13 | Connect Claude "done"; onboarding route; dismissed storage | done = live interactive session with `channel = 1`; `GET /api/projects/{p}/onboarding`; dismissed in daemon settings (Q16) | PRD-03, PRD-05 |
| 14 | Add project safety | marker on the folder as-is before creation; `dry_run` preview; unmarked-dir test; `~`/`is_home` warning, resolved path shown | PRD-03, PRD-05 |
| 15 | Plugin Python on a clean Mac | Q12 launcher `kpython`; welcome shows `plugin_python: missing` | PRD-03, PRD-05 |
| 16 | `--from-source` ran `fetch-python.sh` undeclared | dropped: from-source builds without bundled Python (system fallback) unless fetch was run | PRD-02 |
| 17 | `abuse_probe.py` missing from Owns | added to PRD-03 Owns (`plugins/kanban/tests/fixtures/abuse_probe.py`, with `test_abuse.py`) | PRD-03 |
| 18 | PRD structure + missing edge cases | every PRD has Steps, Interfaces with error states, Edge cases (handling + priority), Out of scope; covered: no release, prereleases, API 403, DELETE with live sessions (tombstone, 410, `project.removed`) | all |
| 19 | Live updates; FR-06 AC; CSV | `session.changed` event (Q17); queued-card explanation AC in PRD-05; CSV FR-05 → PRD-05;PRD-06 | PRD-03, PRD-05 |
| 20 | `--update` and `--all` | version from the tag; `--all` includes the app (Q15); v0.3 installer tests that change listed (`/tests/test_install.py:137-186`) | PRD-02 |
| 21 | Colour test scope; terminal.js tokens | PRD-05's test scoped to its files; PRD-06 AC: terminal.js uses tokens/classes only | PRD-05, PRD-06 |
| 22 | Register/ADRs | Q09 Decided by architect (ADR-001); ADRs cite path:line evidence and tests that pin; ADR-004 Accepted | ADRs |
| 23 | Size ceiling + prune list | ≤ 45 MB per arch (ADR note to change); prune list extended; licence files never pruned | PRD-01 |
| 24 | `-p` origin; base URL; Gatekeeper | `cli-print` label; base URL from `AGENTIC_KIT_REPO` (Q15); README macOS 15+ route: System Settings → Privacy & Security → Open Anyway, and `xattr` | PRD-03, PRD-02, PRD-07 |

## Architect review 2 — disposition
| # | Finding | Resolution | PRD |
|---|---|---|---|
| N1 (Major) | Bundled interpreter chosen without a run-check; environment `PYTHONHOME`/`PYTHONPATH` could break it | `kpython` run-checks each bundled candidate (`-E -B -c 'import sys'`) and falls back to the next / system `python3`; bundled exec'ed with `-E -B`; `--probe` reports the run-check; `daemon_command` uses `-E -B` for the bundled interpreter; tests: failing fake bundled → system, `PYTHONHOME=/bogus` still starts, cargo test on daemon args | PRD-03, PRD-01 |
| N2 (Major) | Behaviour of `server.py` after a 410 undefined | MCP tools keep working on markdown, no embedded UI, one notice with both re-add routes; AC + `test_mcp_channel.py::test_removed_project_local_mode_no_ui` | PRD-03 |
| N3 (Minor) | `plugin_python` guessed daemon-side | `server.py` reports `sys.executable` as `bundled`/`system`/`unknown`, stored in additive `sessions.python`; welcome uses live sessions' reports, daemon lookup only a hint (`plugin_python_source`) | PRD-03 |
| N4 (Minor) | Release step lacked token/repo | `GH_TOKEN: ${{ github.token }}` and `--repo "$GITHUB_REPOSITORY"`; checked by `test_jobs_split` | PRD-04 |
| N5 (Minor) | No proof the signed interpreter runs | post-signing smoke test `-E -c 'import ssl, sqlite3, ctypes, json'` per executable arch (x86_64 only with Rosetta); entitlements change only via that test | PRD-04 |
| N6 (Minor) | AC asserted `permissions/autogenerated/**` | AC: commit what tauri-build generates outside `gen/`, or nothing | PRD-06 |
