# kanban: an agent board over your `.SDD/specs` markdown

The board is a **view over the spec files**; the markdown is the source of truth.
- **A ticket is a spec folder**, and its **columns are the ADLC stages**.
- **Status lives in frontmatter**, so the developer commits it and reviews it with the code, and it works without the plugin too.
- **One board for all your projects** (v0.3): a small per-user daemon, shown in the Kanban desktop app or a browser.
- **Moving a card hands the work to Claude**: a running session picks it up, or a headless run does.
- **You see who is working**: a live indicator on every card, the run's transcript, and a Stop button.

```
.SDD/specs/password-reset/                 ← ticket (a card on the board)
├── README.md        status: developer     ← column; "## Progress" checkboxes = one per stage
├── 01-discovery.md  …                     ← listed under the card's files, opens in the viewer/editor
├── prd/PRD-01-reset-token.md  status: doing   ← sub-task; its checkboxes = sub-sub-tasks
└── tasks/01-write-the-demo.md status: todo    ← extra sub-task (optional)
```

| Level | Where | Values |
|---|---|---|
| Ticket stage (column) | `README.md` frontmatter `status:` | `discovery` · `architect` · `approval` · `developer` · `qa` · `demo` · `e2e` · `done` |
| Sub-task | `prd/*.md`, `tasks/*.md` frontmatter `status:` | `todo` · `doing` · `blocked` · `done` |
| Sub-sub-task | `- [ ]` / `- [x]` checkboxes in any of those files | ticked or not |

Frontmatter looks like this; everything else in the file is yours. After approval the ticket README also carries
`approved_by`, `approved_at` and `approved_hash`:

```markdown
---
title: Password reset page
status: developer
updated: 2026-09-27
---
```

Folders whose names start with `_` or `.` aren't tickets. A README without `status:` shows in Discovery with a warning.

## Install

```bash
claude plugin marketplace add yasinishyn/agentic-kit      # or a local path to the kit
claude plugin install kanban@agentic-kit
```

Restart Claude Code, or run `/reload-plugins`. `/mcp` then lists `plugin:kanban:kanban`. Ask Claude "where is the board?" for the URL.

The kit installer (`--only kanban`) runs the same two commands and adds two rules to the project's
`.claude/settings.json`: `mcp__plugin_kanban_kanban__kanban_approve` under `permissions.ask`, and
`Read(~/Library/Application Support/Kanban/ui.token)` under `permissions.deny` (the `guard` component adds them too).

## The desktop app (macOS)

A small Tauri app that shows the board of every project in one window, with, per ticket, the run transcript, the
approval dialog, the spec editor, a git panel and a terminal in the project folder. It starts the daemon if needed and
holds the UI token in memory.

It is built locally from source (no signed download):

```bash
python3 install.py --only kanban-app --dry-run   # show the commands
python3 install.py --only kanban-app             # build, then copy Kanban.app to ~/Applications (replaces an old copy)
```

It needs `cargo`, the Tauri CLI and the Xcode command line tools; the installer checks them and, when one is missing,
prints the command to install it and stops. It never downloads or runs anything itself. By hand:
`cd plugins/kanban/app && cargo tauri build --bundles app`, then copy
`src-tauri/target/release/bundle/macos/Kanban.app` to `~/Applications`. `--all` and `--update` never build the app.

**No app?** `python3 <plugin>/scripts/daemon.py --open` opens the same board in your browser.

## The board daemon

| | |
|---|---|
| **One per user** | `scripts/daemon.py`, standard library only. Every Claude Code session's MCP server ensures it and registers its project; the app does too. |
| **State** | `KANBAN_HOME` (`~/Library/Application Support/Kanban` on macOS, `$XDG_DATA_HOME/kanban` elsewhere; folder 0700, files 0600): `daemon.json` (port, pid), `client.token`, `ui.token`, `kanban.db` (SQLite: projects, hand-offs, runs, transcripts, approvals), `daemon.log`. |
| **Port** | `127.0.0.1:47821`, or a free port when that one is taken (see `daemon.json`). |
| **Tokens** | Every `/api/*` call needs a bearer token. `client.token` (MCP server, hooks): read, register, claim runs, Claude's own moves, `kanban_approve`. `ui.token` (you): human moves, the approve endpoint, file edits, starting and stopping runs. |
| **Commands** | `daemon.py --ensure` start if needed, print the URL · `--open [--project DIR]` register a project and open the browser (the UI token goes in the URL fragment, never to the server) · `--stop [--cancel-runs]` stop (refused while runs are live unless `--cancel-runs`) |

If the daemon cannot be used, the MCP server falls back to the v0.2 **local mode** (one notice on stderr): the tools
keep working on the markdown, with a board inside the session at `http://127.0.0.1:<port>/` (URL in `.kanban/url`).

## Hand-off: moving a card starts the work

When **you** move a card forward into a working stage (Discovery, Architect, Developer, QA, Demo, E2E) or back for
rework, the daemon records a hand-off. Claude's own moves never create one.

1. **A session with channels picks it up.** Start Claude Code in the project with
   ```bash
   claude --dangerously-load-development-channels plugin:kanban@agentic-kit
   ```
   The hand-off arrives as a channel event; Claude claims it with `kanban_start` and follows the `kanban:sdd` skill.
2. **Nobody claims it within 45 s** (`KANBAN_PICKUP_SECONDS`):
   - with no Claude Code session of the project open, the daemon **starts a headless run** by itself;
   - if any interactive session is open (channel or not), the card offers **Run headless** and **Copy prompt**
     (paste it into any session) instead, so two agents never edit the checkout unasked.
3. **Headless runs** are `claude -p … --output-format stream-json --verbose --permission-mode acceptEdits` in the
   project folder, resumed per ticket. They run under the project's own settings and guard hook, never with
   `--dangerously-skip-permissions`, at most `KANBAN_MAX_RUNS` (4) at once. The card streams the transcript and shows
   denied permissions; **Stop** ends the run.

**Limitation:** sessions in the Claude desktop app's Code tab don't receive channel events. For them **Copy prompt**
or **Run headless** on the card is the way in (a headless run starts by itself only when no session is open).

## Live indicator

| State | On the card |
|---|---|
| running | animated ring, e.g. "working · Architect · 12 s ago" |
| waiting | amber, "waiting for you" (Claude stopped and needs you) |
| queued | pulsing outline (a hand-off nobody has claimed yet, or a run waiting for a slot) |
| stale | grey, "no signal 20 min" (no heartbeat for `KANBAN_STALE_SECONDS`, 15 min) |
| abandoned / failed | red, with the reason (the session's `claude` process is gone, or the run failed) |

The text is announced politely to screen readers; with reduced motion the icon is static.

## Approval

Moving a card into execution (Developer, QA, Demo, E2E, Done) from an earlier column needs a valid approval of the
spec. Moves between those columns, and moves back, are always allowed. A ticket already in execution without an
approval record (approved before v0.3) shows an "approved before v0.3" badge.

- **On the board:** moving a card into Developer or later opens the approval dialog (spec files and hash).
  **Approve and execute** records `approved_by: human (board)` in the README, an approval line in
  `03-architecture.md`, an approval row and a snapshot of the spec for the diff, then moves the card.
- **In chat:** only your own message ("approve X", "move X to developer") counts. Claude calls `kanban_approve`, which
  **always asks you to confirm** (installer rule in `permissions.ask`); it is refused in headless runs and never
  follows a channel event, a file or tool output.
- **Spec changed after approval:** the card shows a badge and the diff; re-entering execution needs a new approval.
- **Without the board daemon** (local mode), `kanban_approve` writes the approval to the ticket's markdown (README
  `approved_*` and the line under the `03-architecture.md` header table) but it is not recorded on a board; it is still
  refused in headless runs.
- A board approval counts as the SDD approval (as does saying "execute" in chat).

Deleting `kanban.db` drops the board's approval records: tickets already in execution then show "approval not
recorded" and need to be approved again.

## Skills

| Skill | Does |
|---|---|
| **`/kanban:sdd <issue or slug>`** | Opens (or continues) the ticket, runs Discovery → Architect into `.SDD/specs/<slug>/` and stops at Approval. Also what a hand-off runs. Follows the project's `sdd` skill when it is installed. |
| **`/kanban:ticket …`** | Alias of `/kanban:sdd`. |
| **`kanban`** | When to move tickets and update sub-tasks and checkboxes (at stage gates, never ahead of the evidence), the channel event contract and the approval rules. |

## Tools

In Claude Code they're `mcp__plugin_kanban_kanban__<tool>`. They edit frontmatter and checkboxes or create files;
they never delete anything.

| Tool | Does |
|---|---|
| `kanban_board` | All tickets by stage, with sub-tasks, progress, files and the board URL (no token) |
| `kanban_new_ticket` | A new spec folder `.SDD/specs/<slug>/README.md` in Discovery |
| `kanban_move` | Move a ticket to a stage (Claude's move: no hand-off; approval rules apply) |
| `kanban_add_subtask` | A new `tasks/NN-<slug>.md` with an optional checklist |
| `kanban_set_status` | Set a PRD or task file's `status:` |
| `kanban_check` | Tick or untick a checkbox |
| `kanban_start` | Claim a ticket's hand-off for this session's run |
| `kanban_heartbeat` | Mark the run alive, with a short progress note |
| `kanban_finish` | End the run: `done`, `needs_input` or `failed`, with a summary |
| `kanban_approval` | Read-only approval state: recorded, valid, actor, hash, board_recorded |
| `kanban_approve` | Record your approval from chat (always asks you first) |

## Hooks

`PostToolUse` sends a heartbeat (at most one per 10 s per session) and `Stop` marks the run "waiting for you"
(`scripts/hook.py`, 2 s timeout). Without a daemon (no `daemon.json`) the hook exits at once, and any error exits 0,
so it never slows or blocks Claude.

## Settings (environment variables)

| Variable | Effect |
|---|---|
| `KANBAN_HOME` | Daemon state folder (default: `~/Library/Application Support/Kanban` on macOS, `$XDG_DATA_HOME/kanban` elsewhere) |
| `KANBAN_DAEMON_PORT` | Daemon port (default `47821`, else a free port) |
| `KANBAN_PICKUP_SECONDS` | Seconds before an unclaimed hand-off goes headless or offers Run headless / Copy prompt (default 45) |
| `KANBAN_STALE_SECONDS` | Seconds without a heartbeat before a run shows as stale (default 900) |
| `KANBAN_MAX_RUNS` | Headless runs at once; more wait in the queue (default 4) |
| `KANBAN_SWEEP_SECONDS` | How often the daemon checks pickups and stale runs (default 10) |
| `KANBAN_POLL_SECONDS` | How often the daemon checks the spec files for changes (default 1) |
| `KANBAN_EDITOR_URL` | "Open in editor" link template, default `vscode://file/{path}`. For Cursor: `cursor://file/{path}` |
| `KANBAN_PROJECT_DIR` | Force the project folder. Otherwise: `CLAUDE_PROJECT_DIR`, then the nearest folder with `.SDD/`, `.claude/` or `.git` |
| `KANBAN_NO_DAEMON=1` | Local mode: no daemon, the v0.2 board inside the session (also what the tests use) |
| `KANBAN_PORT` | Local mode only: fixed board port (default: 8700–8899, derived from the project path) |
| `KANBAN_NO_UI=1` | Local mode only: MCP tools without the board |

Set by the daemon for headless runs: `KANBAN_RUN_ID`, `KANBAN_HANDOFF_ID`. For tests only: `KANBAN_CLAUDE_BIN`,
`KANBAN_DAEMON_API`, `KANBAN_DAEMON_VERSION`, `KANBAN_UI_DIR`, `KANBAN_ROUTES_DIRS`.

## Safety

- **Loopback only:** the daemon listens on `127.0.0.1`; a foreign `Host` or `Origin` is refused and no CORS headers are
  sent (DNS rebinding, cross-site calls). Every `/api/*` call needs a token; tokens never appear in URLs sent to the server.
- **Two tokens:** Claude's `client.token` cannot move cards as you, approve on the board, edit files or start runs.
  The guard hook blocks Bash commands that read `ui.token` or call the move/approve/files/runs endpoints, and the
  installer denies the Read tool on `ui.token`. A guardrail, not a sandbox: the board also flags approvals it did not record.
- **Confined files:** the viewer, editor and tools only touch `.md` files under `.SDD/specs/`; the editor cannot change
  `status` or `approved_*`.
- **Headless runs:** `acceptEdits` plus your project's settings and guard hook, an allow-listed environment, a fixed
  prompt (ticket slug and stage only), one run per ticket.
- **Local only:** nothing leaves your machine. Transcripts are kept 30 days in `kanban.db`.

## Tests

```bash
python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py'
(cd plugins/kanban/app/src-tauri && cargo test)
claude plugin validate plugins/kanban && claude plugin validate .
```
