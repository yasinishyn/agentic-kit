# Kanban v0.3.1: onboarding and UX — Discovery

| Field | Value |
|---|---|
| Spec slug | `kanban-v0-3-1-onboarding-ux` |
| Branch | `main` (single-developer repo) |
| Requested by / date | kit owner, 2026-09-29 |
| Tier | **Full** — upgraded 2026-09-29: adds a release pipeline (CI workflow, third-party build inputs, a bundled Python runtime shipped to users), on top of the onboarding UI and two new endpoints on non-sensitive config |
| Status | Agreed 2026-09-29 (kit owner) |
| Builds on | `kanban-v0-3-agent-board` (v0.3, in Demo) |

## 1. Goal
The Kanban desktop app must be usable from first launch without reading the source. A new user must be guided from an
empty app to a board with their project on it and a Claude session connected that receives board moves; must see at a
glance which Claude sessions are attached to each project and how (with or without hand-off delivery); must be able to
add a project and create a ticket from the app; and must find the board and ticket panel legible at a normal window
size. The plugin README must take a new user from zero (no Rust) to a working board with a quick start. Markdown stays
the source of truth; no hard rule changes.

## 2. Scope
**In scope**
- First-run welcome (empty state) with three steps: add a project → connect Claude → move your first card.
- **Add project** from the app: native folder picker in the desktop app; path entry in browser mode.
- **New ticket** from the board (restores the v0.2 capability lost in v0.3: `ui.html:44`, `POST /api/new`).
- **Connection panel per project**: attached sessions with their kind (CLI with channels · CLI without · desktop Code
  tab · headless run), last seen; a **Connect Claude** action that opens the terminal with the channels flag at
  project level (not buried in a ticket); the reason a Code-tab session cannot receive moves, and what to do instead.
- **Design pass**: responsive board (8 columns fit or collapse; Done/E2E collapsible), quieter header and cards (move
  controls on hover/focus or a menu; meaningful progress), tabbed ticket panel (Overview · Spec · Runs · Terminal ·
  Git) with the spec rendered by default and an Edit mode, consistent spacing/typography tokens, light/dark.
- **Downloadable releases**: a GitHub Actions workflow builds a universal (Apple Silicon + Intel) `Kanban.app`/`.dmg`
  with a bundled Python runtime, on a tag pushed by the developer, and drafts a GitHub Release for the developer to
  publish; unsigned for now with optional signing/notarisation steps that activate when Apple secrets are added.
- **One-line install includes the app**: the kit installer (and so `get.sh`) downloads the matching release of the app on
  macOS, checksum-verified, instead of requiring Rust; installs through the installer avoid Gatekeeper quarantine.
- **README quick start**: prerequisites with the exact Rust/Tauri/Xcode commands, build + install, first launch,
  connect Claude, troubleshooting (python3 not found, port taken, no channel events, Code tab), screenshots.

**Out of scope**
- New board features beyond onboarding (filters, search, assignees), notifications, multi-user.
- Buying an Apple Developer ID or adding its secrets (the developer does it later); Linux/Windows builds.
- Agents publishing releases or pushing tags (the developer does).
- Changing the hand-off, approval or runner rules decided in v0.3.

## 3. Functional requirements
| ID | Requirement | Source | Priority |
|---|---|---|---|
| KB31-FR-01 | With no project registered, the app shows a welcome with the three onboarding steps and their current state | §1 | Must |
| KB31-FR-02 | Add a project from the app: folder picker (app) / path field (browser); the folder must contain `.SDD`, `.git` or `.claude` (v0.3 rule); offers to create `.SDD/specs` when missing | §1; v0.3 B9 | Must |
| KB31-FR-03 | The New-ticket control (restored in v0.3 as B14, Q01) sits in the new header and the welcome's first-card step | Q01 | Must |
| KB31-FR-04 | Connection panel per project: live sessions with kind (CLI+channels, CLI, Code tab, headless) and last seen; updates live | §1 | Must |
| KB31-FR-05 | **Connect Claude** per project: opens the app terminal in the project folder running `claude --dangerously-load-development-channels plugin:kanban@agentic-kit`; browser mode shows the command to copy | §1; ADR-008 | Must |
| KB31-FR-06 | The UI explains, where it matters (connection panel, "queued" cards), that Code-tab sessions get no channel events and offers Copy prompt / Run headless | Q04 (v0.3) | Must |
| KB31-FR-07 | Board fits a 1280×800 window: 8 columns visible or with Done/E2E collapsed; horizontal scroll never hides a column silently | UX review 2026-09-29 | Must |
| KB31-FR-08 | Ticket panel in tabs (Overview · Spec · Runs · Terminal · Git); Spec renders markdown by default, Edit mode keeps the v0.3 editor rules | UX review | Must |
| KB31-FR-09 | Cards: title, stage-relevant badges and live indicator first; move controls on hover/focus or a menu, still keyboard-reachable; progress shows sub-task progress, not the stage checklist | UX review | Should |
| KB31-FR-10 | Header: project switcher, Add project, New ticket, connection status summary; no developer-internal text | UX review | Must |
| KB31-FR-12 | `.github/workflows/kanban-app-release.yml`: on tag `kanban-v*` (and manual dispatch), build a universal macOS app + `.dmg` with `cargo tauri build --target universal-apple-darwin`, run the Python suite and `cargo test` first, attach artefacts with sha256 sums to a **draft** GitHub Release | Q05–Q08 (2026-09-29) | Must |
| KB31-FR-13 | Bundle a relocatable CPython (≥ 3.9) inside the app; the app prefers it over the system `python3`, so a clean Mac needs nothing installed; the plugin's MCP server and hooks also use it (through a launcher) when the app is installed in `~/Applications` or `/Applications`, else the system `python3` (amended in Architect by Q12) | Q06, Q12 | Must |
| KB31-FR-14 | Optional signing + notarisation steps run only when the Apple secrets exist (`APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID`); otherwise the build is unsigned and the release notes say so | Q05 | Must |
| KB31-FR-15 | README "Download" section: get the `.dmg` from Releases, first launch of an unsigned app (right-click → Open, or `xattr -dr com.apple.quarantine`), verify the sha256; build-from-source stays as the alternative | Q05 | Must |
| KB31-FR-16 | The kit installer downloads the app: `kanban-app` gains a download mode — fetch `Kanban.app.zip` of the release matching the kit version (fallback: latest) from this repo's GitHub Releases, verify it against the sums pinned in the kit (`plugins/kanban/app/releases.lock`) when the tag has an entry, else against the release's `SHA256SUMS` with a note (amended in Architect by Q14); the releases come from the repository named by `AGENTIC_KIT_REPO`, so forks use their own (amended in Architect by Q15); unpack into `~/Applications` (replacing an older copy); `--from-source` keeps the cargo build | Q10, Q11, Q14, Q15 (2026-09-29) | Must |
| KB31-FR-17 | `get.sh` one-liner on macOS: the interactive menu offers "Kanban desktop app (download)", pre-selected whenever `kanban` is selected; `--yes` includes it; `--all` includes it (amended in Architect by Q15); `--update` refreshes it when a newer matching release exists; non-macOS skips it with a note | Q10 | Must |
| KB31-FR-18 | The release publishes `Kanban.app.zip` (for the installer) and `Kanban.dmg` (for browser downloads) plus `SHA256SUMS`; tag scheme `kanban-v<plugin version>` | Q11 | Must |
| KB31-FR-11 | README quick start with prerequisites, build/install, first launch, connect Claude, troubleshooting and screenshots | §1 | Must |

## 4. Non-functional requirements
| ID | Requirement |
|---|---|
| KB31-NFR-01 | Plugin runtime stays Python stdlib + static UI. New build inputs allowed: `tauri-plugin-dialog`, a pinned relocatable CPython build (version + sha256 pinned; its licence files ship inside Kanban.app, are listed in `LICENSES/NOTICE.md` and are not copied into user projects — amended in Architect by Q13), and GitHub Actions pinned by commit SHA |
| KB31-NFR-02 | WCAG 2.2 AA: tabs and menus keyboard-operable, visible focus, contrast ≥ 4.5:1 in light and dark |
| KB31-NFR-03 | Security unchanged: new endpoints (add project, new ticket) are UI-token only, loopback, Host/Origin checks, path rules of v0.3; no inline script |
| KB31-NFR-05 | Supply chain: every downloaded CI input is pinned (action SHAs, Python build URL + sha256, `--locked` cargo); the workflow has least-privilege `permissions:` (contents: write only for the release job) and never runs on pull requests from forks with secrets |
| KB31-NFR-04 | No regressions: the v0.3 suites (130 Python, 15 Rust, installer, guard) stay green |

## 5. Existing capabilities to reuse
- `kanban_md.create_ticket` (store); v0.2 create form (`213d2de:plugins/kanban/scripts/ui.html:44,93-96`).
- Daemon: `project_root` marker check and `h_add_project` (client scope today), `live_sessions`, sessions table
  (`kanban_db.py:51`), `project.registered` event, plug-in contracts (`window.kanban.addDrawerPanel`, `cardActions`).
- App: `term_open(project_id, "claude-channels")` preset (`pty.rs:10`), `ui/modules/terminal.js`.
- Session origin: parent `claude` argv (`server.py` `parent_args`) — the desktop app launches `claude` with
  `--output-format stream-json --input-format stream-json` (seen 2026-09-28), a CLI session does not.

## 6. Assumptions
- The desktop app's Code-tab `claude` process keeps a recognisable argv (stream-json input/output).
- Screenshots in the README are made from the synthetic demo project.

## 7. Open questions
See `OPEN-QUESTIONS.md`.

## 8. Risks
| Risk | Impact | Mitigation |
|---|---|---|
| The installer now downloads a binary (changes the v0.3 PRD-08 "never downloads" rule) | Supply-chain surface | only this repo's Releases over HTTPS, sha256 from the same release (integrity, not authenticity until signing), explicit in the menu text; documented |
| Unsigned downloads are blocked by Gatekeeper | Users think the app is broken | README first-launch steps; signing-ready workflow; release notes say unsigned |
| Bundled Python adds size and a third-party binary | Bigger download; supply-chain surface | pinned version + sha256; licence recorded; universal build only once |
| App-bundled daemon version differs from the plugin's | api mismatch → local mode | versions released together; `--ensure` replaces an idle older daemon (v0.3) |
| Session-origin detection by argv is heuristic | Wrong label in the connection panel | Label "unknown" when unsure; never gate behaviour on it |
| A design pass touches many UI files at once | Merge conflicts with v0.3 Demo fixes | Start Developer after v0.3 is handed off |
| Adding projects from the UI widens the registry surface | Wrong folder registered | Marker rule, confirmation of the resolved path, remove-project action |
