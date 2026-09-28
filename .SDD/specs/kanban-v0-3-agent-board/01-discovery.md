# Kanban v0.3: desktop app, agent hand-off on move, live work indicator, kanban:sdd skill — Discovery

| Field | Value |
|---|---|
| Spec slug | `kanban-v0-3-agent-board` |
| Branch | `main` (the developer creates a feature branch, e.g. `feature/kanban-v0-3`) |
| Requested by / date | kit owner, 2026-09-28 |
| Tier | Full ADLC — a new desktop application with an embedded database, an integration that pushes work into Claude Code sessions (channels + headless runs), an embedded terminal (process execution), and a changed approval mechanism |
| Status | Agreed 2026-09-28 (kit owner); amended in Architect by Q09–Q13 (see OPEN-QUESTIONS.md) |

## 1. Goal
The kanban plugin must become a local control surface for SDD work instead of a passive view. It must ship as a real
macOS desktop application (Tauri shell) with an embedded SQLite database for operational state, serving every project
on the machine; the spec markdown stays in each project's `.SDD/specs/` folder and remains the source of truth (the main
difference from paperclip, which keeps work items in its database). A human moving a ticket on the board must hand the
next stage to Claude: first by pushing an event into the running Claude Code session through the plugin's MCP server
(Claude Code channels), and when no session listens, by starting a headless Claude run the app supervises. Tickets and
sub-tasks under active work must show a live indicator driven by real activity, with the run transcript, a Stop
control, an embedded terminal, a spec viewer/editor and a read-only git panel available in the app. Approval to leave
the Approval stage must be an explicit, recorded human decision (a confirm dialog on the board, or a chat request
handled by a skill). The plugin must ship a self-contained `kanban:sdd` skill that specs an issue into a ticket and
stops at Approval. The kit's hard rules (no push, no deploy, no agent commits by default, local only) keep holding.

## 2. Scope
**In scope**
- **Desktop app** (`plugins/kanban/app/`): Tauri 2 shell for macOS arm64 (portable code, no other platform builds),
  built locally by the kit's installer into `~/Applications/Kanban.app`.
- **Board daemon** (Python, standard library): the existing board server grows into a long-running daemon the app
  supervises; embedded SQLite (`sqlite3`) under the user's app-data folder for projects, runs, run events, hand-offs,
  approvals and terminal sessions. The markdown is never copied into the DB beyond caches.
- **Multi-project**: the app lists projects (registered by the MCP server on start or added by hand) and switches between them.
- **Board UX**: drag-and-drop between columns with a keyboard alternative; a ticket drawer (sub-tasks, files, runs,
  approvals) sized for lead-hunt scale (up to ~50 PRDs per ticket, ~10 tickets).
- **Hand-off**: a human move records a hand-off; the plugin MCP server delivers it into the running session as a
  channel event; otherwise the daemon starts or resumes a headless `claude -p --output-format stream-json` run per
  ticket (paperclip `claude_local` pattern), one live run per ticket, duplicates coalesced.
- **Live indicator**: run state (queued, running, succeeded, failed, cancelled, stale) from the daemon; new MCP tools
  let an interactive session claim, heartbeat and finish work on a ticket or PRD.
- **App tools**: live run transcript + Stop; embedded terminal per project (can launch `claude` with channels enabled);
  spec viewer/editor for the ticket markdown with a diff since approval; read-only git status panel (branch, changed and
  staged files, the hand-off git block).
- **Approval**: moving out of Approval requires a confirm dialog that records who/when/spec hash in the ticket README;
  a chat request ("move X to developer / approve X") handled by a skill records the same.
- **Skills**: new self-contained `kanban:sdd` (spec an issue: ticket → Discovery → Architect → stop at Approval; defers
  later stages to the project `sdd` skill when present); `kanban:ticket` kept as an alias; `kanban` skill extended
  with moving/approving via prompt and the channel-event contract.
- **Kit integration**: installer component to build/install the app, guard-hook rules for the board tokens and approval endpoints, the `sdd` skill amendment (Q10, Q11), README updates, plugin version bump, tests.

**Out of scope (explicitly)**
- Linux and Windows builds; code signing, notarisation, auto-update, GitHub release binaries (a human may add later).
- Hosting, remote access, user accounts; anything listening beyond `127.0.0.1`.
- Storing specs, tickets or statuses in the database as the source of truth.
- Paperclip's companies, org charts, budgets, hiring and multi-agent scheduling (timers/heartbeats on a clock).
- Git write actions from the app (commit, push, branch) — the developer runs them.
- Changing the project `sdd` skill beyond the approval/trigger amendment decided in Q10–Q11.

## 3. Functional requirements
| ID | Requirement | Source | Priority |
|---|---|---|---|
| KB3-FR-01 | A macOS desktop app (Tauri 2) opens the board in a native window and keeps running independently of Claude Code sessions | §1; Q01 | Must |
| KB3-FR-02 | The app starts/supervises one board daemon per user; if a daemon is already running it attaches to it | §1; today the board lives in the MCP process (`server.py:303-321`) | Must |
| KB3-FR-03 | Operational state is stored in an embedded SQLite DB in the app-data folder: projects, sessions, runs, run events, hand-offs, approvals (terminal sessions stay in the app process, Q13); schema versioned with forward migrations | Q01, Q13 | Must |
| KB3-FR-04 | The app lists projects and switches between them; the MCP server registers its project with the daemon on start | §1; lead-hunt and rmsl both use the board | Must |
| KB3-FR-05 | Specs, tickets, statuses and checkboxes remain markdown in `<project>/.SDD/specs/`; the DB never becomes their source of truth and can be deleted without losing work | Q03 note ("main diff with paperclip") | Must |
| KB3-FR-06 | Cards can be dragged between columns; move buttons remain as the keyboard alternative | user item 2 | Must |
| KB3-FR-07 | A human move to a working stage records a hand-off (ticket, from, to, actor, time) | user item 2 | Must |
| KB3-FR-08 | The MCP server delivers the hand-off into its session as `notifications/claude/channel` (fixed template naming ticket, stage and the skill to run) and declares `experimental.claude/channel` with `instructions` | Q02; channels reference | Must |
| KB3-FR-09 | Delivery is confirmed only when Claude calls a kanban tool for that hand-off (`kanban_start`); unconfirmed hand-offs stay "queued" on the board | channels are fire-and-forget (channels reference, "Notification format") | Must |
| KB3-FR-10 | Fallback: if no channel session picks a hand-off up within a timeout (or the user clicks "Run headless"), the daemon runs `claude -p --output-format stream-json` in the project, resuming the ticket's stored session id | Q02; paperclip `claude_local/execute.ts` | Must |
| KB3-FR-11 | At most one live run per ticket; duplicate hand-offs while queued/running are coalesced; a second run is refused | paperclip wakeup coalescing, `executionRunId` lock | Must |
| KB3-FR-12 | Moves made by Claude through MCP tools never produce a hand-off back to Claude | derived (loop prevention) | Must |
| KB3-FR-13 | New MCP tools `kanban_start` (claim ticket/PRD, optional hand-off id), `kanban_heartbeat` (note), `kanban_finish` (outcome, summary) | user item 3 | Must |
| KB3-FR-14 | Cards and sub-tasks with a queued/running run show an animated indicator plus text ("working · Architect · 12 s ago"); runs without a heartbeat past the TTL show "stale", never "working" | user item 3; lead-hunt shows 0 `doing` PRDs while in Developer | Must |
| KB3-FR-15 | Ticket drawer shows run history and a live transcript for headless runs (streamed from stream-json), with a Stop button that terminates the run's process group | Q-tools; paperclip `LiveRunWidget.tsx` | Must |
| KB3-FR-16 | Embedded terminal per project (PTY), which can start `claude` with the kanban channel flag preset | Q-tools | Must |
| KB3-FR-17 | Spec viewer/editor: render and edit the ticket's markdown files (writes confined to `.SDD/specs/`), and show a diff of spec files since the recorded approval | Q-tools | Should |
| KB3-FR-18 | Read-only git panel: branch, changed/staged files for the project, and the latest hand-off git block; no git write actions | Q-tools; CLAUDE.md hard rule 3 | Should |
| KB3-FR-19 | Leaving Approval for Developer requires a confirm dialog; the approval (approver, time, sha256 of the spec files) is written to the ticket README and the DB | Q03 | Must |
| KB3-FR-20 | A chat request to move or approve a ticket is handled by a skill that calls `kanban_move` / `kanban_approve`, recording the same approval with actor `human (chat)` | Q03 | Must |
| KB3-FR-21 | Skill `kanban:sdd`: create or continue the ticket for an issue, run Discovery → Architect writing spec files into it with self-contained templates, keep status current, stop at Approval; hand later stages to the project `sdd` skill when installed | Q04, user item 4 | Must |
| KB3-FR-22 | `kanban:ticket` remains and routes to `kanban:sdd`; the `kanban` skill documents channel events, run tools and approval | derived (compatibility) | Should |
| KB3-FR-23 | The kit installer can build and install the app locally (`--only kanban-app`), checking prerequisites and printing what is missing | Q-distribution | Must |
| KB3-FR-24 | Without the app or daemon, the plugin keeps today's behaviour (MCP tools + embedded web board) | NFR-07 | Must |

## 4. Non-functional requirements
| ID | Requirement |
|---|---|
| KB3-NFR-01 | Runtime dependencies: the daemon, MCP server and skills stay Python 3.9+ standard library only. Build-time only: Rust (stable) and `tauri-cli` for the app (no Node: static UI, vendored xterm.js — ADR-001) |
| KB3-NFR-02 | Accessibility: WCAG 2.2 AA — drag has a keyboard alternative, visible focus, loader text in an `aria-live` region, `prefers-reduced-motion` respected, terminal and editor reachable by keyboard |
| KB3-NFR-03 | Security: daemon listens on `127.0.0.1` only, Host-header check, a per-install random token required for every write and for terminal/run endpoints (the terminal is remote code execution if exposed), CSP kept strict; file access confined to registered projects' `.SDD/specs/` except the PTY |
| KB3-NFR-04 | Prompt-injection hygiene: channel content and headless prompts use fixed templates; ticket titles/paths passed as quoted data; only events originating from the local board are forwarded |
| KB3-NFR-05 | Headless runs obey the project's `.claude/settings.json` and guard hook; no `--dangerously-skip-permissions`; permission mode per Q06 |
| KB3-NFR-06 | Performance: board renders ≤ 200 ms for 10 tickets × 50 PRDs; live updates via SSE (polling fallback), no busy loops |
| KB3-NFR-07 | Resilience: if channels, the daemon, the DB or the app fail, Claude keeps working by editing markdown; a deleted DB is rebuilt empty without data loss |
| KB3-NFR-08 | No regressions: `plugins/kanban/tests/test_kanban.py` and `tests/test_install.py` stay green; new Python code covered by stdlib `unittest`; Tauri shell covered by `cargo test` + a smoke launch |
| KB3-NFR-09 | Audit: hand-offs, approvals and run events are append-only rows with timestamps and actor; every run state change is also written as a run event |

## 5. Existing capabilities to reuse
- Store and markdown editing: `plugins/kanban/scripts/kanban_md.py` (`move_ticket` 195, `set_status` 222, `check` 233, `safe_path` 127, `write_atomic` 84, `read_ticket` 148).
- MCP stdio loop and tool table: `plugins/kanban/scripts/server.py:33-135`; capabilities returned at `server.py:105-108`.
- Web board, markdown viewer, safety headers: `server.py:138-274`, `plugins/kanban/scripts/ui.html`.
- Per-project port and `.kanban/url` discovery with health check: `server.py:282-321`.
- Skills: `plugins/kanban/skills/{kanban,ticket}/SKILL.md`; stage rules `.claude/skills/sdd/`; templates `.SDD/templates/`.
- Tests: `plugins/kanban/tests/test_kanban.py` (store, MCP over subprocess, HTTP board); installer `tests/test_install.py:118`.
- Installer component pattern: `install.py:39`, `install.py:203-209`, `install.py:263-304`.

## 5b. Precedent: paperclip (github.com/paperclipai/paperclip, read 2026-09-28)
- Local Node server + React board in the browser, no desktop shell; `npx paperclipai onboard`; Postgres holds work items.
- Agents are woken through one queue (`heartbeat.wakeup`, sources timer/assignment/on_demand), one run per agent, duplicates coalesced (`doc/spec/agent-runs.md` §8).
- `claude_local` adapter runs `claude --print --output-format stream-json --verbose --resume <sessionId>`; session id kept per task; falls back to a fresh session if resume fails.
- Board shows a pulsing "Live" dot for queued/running runs (`ui/src/lib/liveIssueIds.ts`); `LiveRunWidget.tsx` streams the transcript with Stop; realtime over websocket with polling fallback.
- Approval stages are a runtime rule with recorded decisions (`docs/guides/execution-policy.md`).

## 6. Assumptions
- Channels accept a Python stdio server: the contract is JSON-RPC (`experimental['claude/channel']: {}` + `notifications/claude/channel` with `content` and identifier-only `meta` keys), per code.claude.com/docs/en/channels-reference.
- Custom channels are not on the preview allowlist: sessions start with `claude --dangerously-load-development-channels plugin:kanban@agentic-kit`. Pro/Max users need no org setting; Team/Enterprise need `channelsEnabled`.
- Events are dropped silently if the session did not opt in, and queue while Claude is busy.
- Target machine: macOS arm64 with Xcode Command Line Tools; `python3` ≥ 3.9 on PATH at runtime.
- The kit owner installs Rust (rustup) and Node ≥ 18 before the app build (today: no Rust, Node 16.15).

## 7. Open questions
See `OPEN-QUESTIONS.md`.

## 8. Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Channels are a research preview; flag or contract changes | Hand-off silently stops | Pick-up confirmation (FR-09), queued state, headless fallback |
| Embedded terminal and run endpoints are code execution on the machine | Local RCE via a malicious web page (DNS rebinding / CSRF) | Loopback + Host check + per-install token held only by the Tauri shell and MCP server; no CORS; no endpoint without the token |
| Headless run and interactive session edit the same checkout | Conflicting edits | One live run per ticket; the board shows the interactive session's claim; headless only when unclaimed |
| Approval dialog turns into a click-through | Spec executed without review | Dialog shows the spec file list + hash; approval written into README for the developer's commit review |
| Scope size (Tauri, daemon, DB, PTY, editor, skills) | Long delivery | PRDs as vertical slices; app shell after the daemon so value lands early |
| Build prerequisites missing (Rust, Node ≥ 18) | App cannot be built | Installer prerequisite check; board still usable through the browser |
| Sidecar Python not found in a GUI-launched app (PATH differs) | App cannot start its daemon | Resolve `python3` via login shell / known paths; clear error screen |
