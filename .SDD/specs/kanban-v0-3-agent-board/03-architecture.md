# Kanban v0.3 — Architecture

| Field | Value |
|---|---|
| Spec | `kanban-v0-3-agent-board` · [01-discovery.md](01-discovery.md) (Agreed 2026-09-28) |
| Tier | Full |
| Status | Draft rev 2 (architect review 1: CHANGES REQUESTED, findings addressed below) — waiting for the user's approval ("execute") |

## 1. Bounded context
**SDD work tracking on the developer's machine.** The *spec* (ticket README, discovery, architecture, ADRs, PRDs,
checkboxes) lives in each project's `.SDD/specs/<slug>/` and is owned by git and the developer. The kanban system owns
only *operational* facts about that work: which projects exist, who moved what, which hand-offs are pending, which runs
are live, what they printed, and which approvals were given. Anything the developer must commit (status, approval
record) is written to markdown; everything ephemeral lives in SQLite.

```
                 ┌──────────────────── Kanban.app (Tauri 2, Rust) ─────────────────────┐
                 │  window → http://127.0.0.1:<port>/   (UI token injected, in memory)  │
                 │  terminal: portable-pty ⇄ Tauri IPC ⇄ xterm.js   (never over HTTP)   │
                 │  supervises ─────────────┐                                           │
                 └──────────────────────────┼──────────────────────────────────────────┘
                                            ▼
 Claude session A (CLI, channels flag)        ┌────────── kanband (Python daemon, one per user) ─────────┐
   └─ server.py (MCP stdio) ── client token ► │ HTTP API + streamed events on 127.0.0.1                    │
        ▲ notifications/claude/channel ◄───── │ scopes: client token (Claude) · UI token (human)           │
 Claude session B (Code tab, no channels)     │ board · moves · hand-offs · runs · approvals · specs · git │
   └─ server.py (MCP stdio) ── client token ► │ runner: claude -p --output-format stream-json (headless)   │
   └─ hooks (PostToolUse/Stop) ── heartbeat ─►│ SQLite <KANBAN_HOME>/kanban.db  (flock single instance)    │
                                              └───────────────┬────────────────────────────────────────────┘
                                                              │ reads/writes (atomic, confined)
                                                              ▼
                                         <project>/.SDD/specs/<slug>/*.md   (source of truth, committed)
```

## 2. Domain model (pure functions, no I/O) — `scripts/kanban_rules.py`
| Concept | Rule (pure) | Enforced in |
|---|---|---|
| **Stage** | `STAGES` unchanged. `WORKING = {discovery, architect, developer, qa, demo, e2e}`. `EXECUTION = {developer, qa, demo, e2e, done}` | domain |
| **Transition** | `transition_allowed(src, dst, approval)` → ok \| `approval_required` \| `spec_changed`. Rule (Q09): entering `EXECUTION` from a stage outside it needs a **valid** approval (hash matches); moves inside `EXECUTION` need an approval **record** (any hash; a changed spec is a warning badge, not a block); backward moves are always allowed | `kanban_md.move_ticket` (the only writer of a README stage). `set_status` refuses README files (use `kanban_move`); every tool validates enums server-side |
| **Hand-off kind** | `handoff_kind(src, dst, actor)` → `None` unless actor is `human (board)`; `"start"` for forward moves into `WORKING`; `"rework"` for backward moves into `WORKING`; `None` for `approval`/`done` targets and same stage (Q05) | daemon human-move handler |
| **Spec hash** | `spec_hash(files)` = sha256 over the sorted normalised contents of `01-*.md`, `02-*.md`, `03-*.md`, `adr/*.md`, `prd/*.md`, `OPEN-QUESTIONS.md` (README excluded). Normalise: drop frontmatter keys `status`, `updated`, `approved_by`, `approved_at`, `approved_hash`; drop lines starting `Approved for execution by `; render every checkbox as unchecked. So approving and ticking boxes never change the hash; editing text does | domain |
| **Approval** | `approval_record(fields)`; `approval_valid(fields, current_hash)`; approval text line `Approved for execution by <actor> on <YYYY-MM-DD> (spec <hash12>)` | `kanban_md.approve` |
| **Actors** | literals `human (board)`, `human (chat)`, `claude`, `runner` | server-side only; never taken from the request body |
| **Hand-off lifecycle** | `queued → delivered → claimed → done`; `coalesced` (target equals the live run's stage); `superseded` (a newer human move on the same ticket); `requeued` when a finished run has a pending coalesced hand-off with a different target | daemon + `kanban_db` |
| **Run state** | `queued → running ⇄ waiting → succeeded \| failed \| cancelled \| abandoned`. Terminal states are **sticky** (no transition out). `view_state(run, now, ttl)` → `stale` for running/waiting past TTL | `kanban_db` transition function, tested |
| **Claim** | `kanban_start(handoff_id)`: first claim wins; the run that already owns the claim (same `run_id`) gets `ok` (idempotent); others `already_claimed`; superseded → `superseded` | SQLite `BEGIN IMMEDIATE` + partial unique index `runs(project_id, ticket) WHERE status IN ('queued','running','waiting')` |

## 3. Components
| Component | Path | Owner PRD |
|---|---|---|
| Domain rules | `scripts/kanban_rules.py` (new) | PRD-01 |
| Markdown store | `scripts/kanban_md.py` (+ `approve`, guarded `move_ticket`, `set_status` README refusal) | PRD-01 |
| Operational store | `scripts/kanban_db.py` (all migrations, claim/coalesce/supersede accessors, run transitions) | PRD-02 |
| Daemon core + plug-in contract | `scripts/daemon.py`, `scripts/mdview.py` | PRD-02 |
| Route modules (auto-discovered `scripts/routes_*.py`) | `routes_runs.py` (PRD-03, then PRD-04), `routes_specs.py` (PRD-05) | — |
| Runner | `scripts/runner.py` | PRD-04 |
| MCP server | `scripts/server.py` | PRD-02 → PRD-03 |
| Hooks | `hooks/hooks.json`, `scripts/hook.py` | PRD-03 |
| Board UI core | `ui/index.html`, `ui/app.js`, `ui/board.js`, `ui/board.css` | PRD-02 |
| UI modules (auto-discovered `ui/modules/*.js|*.css`) | `runs.*` (PRD-03 → PRD-04), `transcript.js` (PRD-04), `specs.*` (PRD-05), `terminal.js` (PRD-07) | — |
| Desktop shell | `app/**` | PRD-07 |
| Skills | `skills/sdd/**`, `skills/ticket/`, `skills/kanban/`; kit `sdd` skill amendment | PRD-06 |
| Kit guard + installer + docs | `.claude/hooks/guard_bash.py`, installer, READMEs | PRD-08 |

### 3.1 Daemon plug-in contract (PRD-02, normative)
- A route module exports `register(ctx)`. `ctx` offers: `route(method, pattern, handler, scope)` where scope is
  `read | client | ui`; `db` (thread-local connection factory); `project(id) → root` (registry lookup only; clients never
  send paths); `publish(project_id, event, data)`; `live_sessions(project_id, channel=None)`; `record_approval(project, ticket, actor)`; `on_startup(fn)`; `on_human_move(fn(project, ticket, src, dst,
  handoff))`; `settings`.
- `daemon.py` imports every `scripts/routes_*.py` at start (sorted); a module that fails to import is logged and skipped.
- Coalescing, superseding and the `handoff.created` publish happen in PRD-02's move handler; PRD-03 adds delivery.

### 3.2 UI plug-in contract (PRD-02, normative)
- `GET /ui/modules` lists `ui/modules/*.js` and `*.css`; `app.js` loads them after the board.
- `window.kanban = { api(path, opts), stream(path, onEvent), decorateCard(fn(card, ticket)), addDrawerPanel(id,
  title, render), beforeMove(fn(ticket, src, dst) → Promise<boolean>), cardActions(fn(ticket) → [{label, run}]) }`.
- No inline scripts or `on*=` attributes anywhere; every dynamic string is set with `textContent` or escaped.

### 3.3 MCP tool contract (normative; used by PRD-06 skills)
| Tool | Args | Token scope | Notes |
|---|---|---|---|
| `kanban_board`, `kanban_new_ticket`, `kanban_move`, `kanban_add_subtask`, `kanban_set_status`, `kanban_check` | as v0.2 | client | `kanban_move` actor `claude`, never creates a hand-off; guarded by `transition_allowed` |
| `kanban_start` | `ticket`, `handoff_id?`, `subtask?` | client | claim; returns `ok run_id` \| `already_claimed` \| `superseded` |
| `kanban_heartbeat` | `note?` | client | updates the caller's run |
| `kanban_finish` | `outcome: done \| needs_input \| failed`, `summary` | client | terminal state for the caller's run |
| `kanban_approval` | `ticket` | client | read-only: `{recorded, valid, actor, at, hash12, board_recorded}` — the verification the `sdd` skill uses |
| `kanban_approve` | `ticket` | client → `POST …/approve-chat` | actor `human (chat)`; the daemon refuses it for headless sessions; writes the same record as the board (markdown + approvals row + snapshot); documented as never auto-allowed, and the installer adds it to `permissions.ask`, so Claude Code's permission prompt is the human confirmation |

## 4. Process topology (ADR-003)
- **One daemon per user.** `KANBAN_HOME` = `~/Library/Application Support/Kanban` (macOS), `$XDG_DATA_HOME/kanban`
  elsewhere. Single instance: `fcntl.flock` on `KANBAN_HOME/daemon.lock`, taken before bind and held for life;
  `daemon.json` (`port, pid, api, version`) written after bind.
- **Ensure:** `daemon.py --ensure` (MCP server, app): healthy + same `api` → reuse; newer `version` and no live run →
  replace the idle daemon; otherwise reuse and report. Detached spawn with `start_new_session=True`, stdio → devnull /
  `KANBAN_HOME/daemon.log`. `api` mismatch that cannot be resolved → MCP *local mode* (v0.2 behaviour) + one notice.
- **Stop:** `daemon.py --stop` refuses while runs are live unless `--cancel-runs`.

## 5. Hand-off flow (ADR-004)
1. Human move (UI token) → `move_ticket` (markdown, guarded) → `handoff_kind`. The UI's `beforeMove` opens the approval
   dialog first when `transition_allowed` would answer `approval_required` (PRD-05).
2. Hand-off row: newer human move supersedes older unclaimed hand-offs of the ticket; target equal to a live run's
   stage → `coalesced`; publish `handoff.created`.
3. Each registered MCP server of the project holds a streamed subscription (fetch/chunked, client token) and turns
   **new** events (created after it subscribed) into `notifications/claude/channel` (stdout lock shared with responses;
   nothing is written before `initialize` is answered). Sessions without channels drop it silently.
4. Claude calls `kanban_start(ticket, handoff_id)` → claim → run `running` (kind `interactive`, `claude_pid`, pid start
   time). Losers get `already_claimed`; stale ones `superseded`.
5. **Channel capability detection:** the MCP server reads its parent's argv (`ps -o args= -p <ppid>`) and registers
   `channel=true` only if it contains `--channels` or `--dangerously-load-development-channels` naming
   `plugin:kanban@…` (or `server:kanban`). A session counts as live only while its subscription is open.
6. No claim within `KANBAN_PICKUP_SECONDS` (45): if no live channel session exists → daemon auto-starts headless;
   otherwise the card offers "Run headless" / "Copy prompt".
7. **Headless run** (PRD-04): argv `claude -p <headless_prompt> --output-format stream-json --verbose --permission-mode
   acceptEdits [--resume <id>]`; cwd = project root; env from an allow-list (`HOME USER LOGNAME LANG LC_* TMPDIR SHELL`,
   resolved `PATH`, `KANBAN_HOME`) plus `CLAUDE_PROJECT_DIR=<root>`, `KANBAN_RUN_ID`, `KANBAN_HANDOFF_ID`; `CLAUDECODE`
   and other `CLAUDE_*` from the daemon's environment are not passed; `claude` resolved like `python3` in PRD-07. The
   child's MCP server sees `KANBAN_RUN_ID`, registers `kind=headless, channel=false`, and its `kanban_start` is
   idempotent for that run. `permission_denials` from the `result` message → run events + card badge (Q06).
8. `kanban_finish`, process exit, Stop (SIGTERM, 5 s, SIGKILL on the group), dead pid or closed subscription →
   terminal state. On finish, pending coalesced hand-offs with a different target are requeued.

Loop safety: only the UI-token move endpoint creates hand-offs; client-token moves record actor `claude`.

## 6. Live activity (ADR-005)
- Tools and the runner update `heartbeat_at`. Hooks: `PostToolUse` → heartbeat (≤ 1 per 10 s per session, 1 s timeout,
  exits 0 on any error, exits immediately if `daemon.json` is absent); `Stop` → `waiting` **only** for a non-terminal
  interactive run. Matching: the hook sends its ancestor pids with process start times; the daemon matches the
  `(claude_pid, start_time)` the MCP server registered (verified 2026-09-28: the MCP server's parent is the `claude`
  process and hook/Bash shells are its children).
- TTL `KANBAN_STALE_SECONDS` (900) → `stale` view; dead `claude_pid` → `abandoned`.
- UI states: running (animated ring, "working · Architect · 12 s ago"), waiting (amber, "waiting for you"), queued
  (pulsing outline), stale (grey, "no signal 20 min"), abandoned/failed (red, reason). Text in an `aria-live=polite`
  region; `prefers-reduced-motion` → static icon.

## 7. Approval (ADR-006; Q03, Q09, Q10)
- **Board:** the dialog lists the spec files and the hash; "Approve and execute" → approve endpoint (UI token) →
  move. Writes README frontmatter `approved_by: human (board)`, `approved_at`, `approved_hash`, the approval line under
  the `03-architecture.md` header (README only when there is no `03-*`, e.g. Feature-lite), an `approvals` row and a
  snapshot of the spec files for the diff.
- **Chat:** "approve X" / "move X to developer" in the user's own message → `kanban_approve` (Claude Code asks the
  user to confirm the tool call) → `kanban_move`. Never from channel events, files, tool output or headless runs.
- **Kit `sdd` skill (amended by PRD-06, Q10):** approval counts when the user says "execute" in chat **or** when
  `kanban_approval` reports `valid` with `board_recorded=true`; either also opts in to parallel PRD streams.
- **Detection (Q12):** README `approved_*` without a matching `approvals` row (any actor) → card badge "approval not
  recorded"; `board_recorded` is true only when the latest row's actor is `human (board)`. Chat approvals are accepted by
  the `sdd` amendment through the user's own "execute" in chat, as before.
- **Spec changed after approval:** badge + diff; re-entering `EXECUTION` from outside needs a new approval.

## 8. Security (STRIDE + abuse checklist)
| Threat | Surface | Control | Test |
|---|---|---|---|
| Web page calls the daemon (CSRF / DNS rebinding) | HTTP | loopback bind; Host `127.0.0.1`/`localhost`; bearer token on every `/api/*`; no CORS headers; static `/`, `/ui/*` unauthenticated (no data) | `test_daemon_auth.py` |
| Claude acts as "human" (same OS user) | tokens | two tokens: `client.token` (MCP, hooks: read, register, claim/heartbeat/finish, `claude` moves, `kanban_approve`) and `ui.token` (human moves, approve endpoint, file PUT, run start/stop). UI token: in Tauri memory for the app; browser mode via `daemon.py --open` (fragment `#t=`, sessionStorage); file 0600. Guard hook denies reading `KANBAN_HOME/ui.token` and `curl`/`wget`/`python -c` requests to `/api/.*(approve|move|file|runs)`. Detection badge (§7). Residual risk accepted by the owner (Q12) | `test_daemon_auth.py` scope matrix; guard tests |
| Events stream auth | streamed events | fetch streaming with `Authorization` header (no `EventSource`, no token in URLs) | auth test on `/api/events` |
| XSS → terminal RCE | UI + Tauri IPC | CSP `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ipc: http://ipc.localhost; frame-ancestors 'none'`; no inline script; transcripts and markdown rendered via `textContent`/escaped subset; Tauri capability `remote.urls` = exact daemon origin, terminal commands on the main window only; `term_open(project_id)` resolved by Rust via the daemon registry, never a path | CSP header test; `<img onerror>` transcript fixture; cargo capability test |
| Headless run escapes intent | runner | `acceptEdits` + project settings + guard hook; env allow-list; fixed prompt template; one live run per ticket; `KANBAN_MAX_RUNS` 4 | `test_runner.py` argv + env |
| Path traversal / frontmatter bypass | spec editor | `safe_path` + symlink check; `.md` only; `If-Match`; PUT refuses changes to `status`, `approved_*` keys | `test_specs_api.py` |
| Prompt injection via ticket text | channel/headless | slug (`^[a-z0-9][a-z0-9-]{0,59}$`) + stage names only | `test_rules.py` |
| Repudiation | moves, approvals, runs, stops | append-only `handoffs`, `approvals`, `run_events` (every run transition logged as an event); approval in committed markdown | `test_db.py` |
| Info disclosure | transcripts, tokens | `KANBAN_HOME` 0700, files 0600; transcripts kept 30 days; `kanban_board` prints the URL without token | `test_db.py`, `test_daemon.py` |
| DoS | streams, runs | max 32 subscribers; one run per ticket; max 4 headless runs | `test_daemon.py` |
| git index contention | git panel | `git --no-optional-locks -c core.fsmonitor= status --porcelain=v1`, `rev-parse --abbrev-ref HEAD`; argv lists | `test_specs_api.py` |

## 9. Reuse map
- `kanban_md.py`: `split_frontmatter` 53, `set_fields` 70, `write_atomic` 84, `set_checkbox` 105, `safe_path` 127,
  `read_ticket` 148, `move_ticket` 195 (guarded), `set_status` 222 (README refused), `render_board` 240.
- `server.py`: MCP loop 93-135 (stdout lock); `render_md` 139-218 → `mdview.py`; safety headers 229-237 → daemon.
- `ui.html` card rendering 57-74 → `ui/board.js` (kept for local mode).
- paperclip precedents (discovery §5b): run states, coalescing, live dot, transcript + Stop, resume per task.

## 10. Test strategy
| Layer | Tool | Files (owner) |
|---|---|---|
| Domain | `unittest`, literal vectors | `tests/test_rules.py` (PRD-01) |
| Markdown store | `unittest` | `tests/test_kanban.py` (PRD-01 → PRD-02 → PRD-03, serial) |
| Test isolation | every test uses a temp `KANBAN_HOME` and either `KANBAN_NO_DAEMON=1` or a test daemon from `tests/helpers.py` | `tests/helpers.py` (PRD-02) |
| SQLite store | `unittest` | `tests/test_db.py` (PRD-02) |
| Daemon HTTP/stream/auth/plug-ins | `unittest` + `http.client` against a daemon subprocess | `tests/test_daemon.py`, `tests/test_daemon_auth.py` (PRD-02) |
| MCP + channel + tools | stdio subprocess | `tests/test_mcp_channel.py`, `tests/test_runs_api.py`, `tests/test_hook.py` (PRD-03) |
| Runner | fake `claude` on PATH printing stream-json and asserting its env | `tests/test_runner.py`, `tests/fixtures/fake_claude.py` (PRD-04) |
| Specs/approval API | `unittest` | `tests/test_specs_api.py` (PRD-05) |
| Skills | frontmatter, links, tool names vs §3.3 | `tests/test_skills.py` (PRD-06) |
| Tauri | `cargo test` + `cargo tauri build --bundles app` + smoke launch | `app/src-tauri` (PRD-07) |
| Installer, guard | existing test files | PRD-08 |
| UI | Demo stage in the built-in browser (browser mode) + app smoke | `06-demo.md` |

Gate for every Python PRD: `python3 -m unittest discover -s plugins/kanban/tests -p 'test_*.py' -v`,
`python3 tests/test_install.py`, `claude plugin validate plugins/kanban && claude plugin validate .`.

## 11. PRDs and waves
| PRD | Slice | Depends on | Wave | Parallel with |
|---|---|---|---|---|
| [PRD-01](prd/PRD-01-domain-rules-and-approval-record.md) | Domain rules, transition guard, approval record | — | 1 | PRD-06 |
| [PRD-06](prd/PRD-06-skills-kanban-sdd-ticket-kanban.md) | Skills `kanban:sdd`, `ticket`, `kanban` + kit `sdd` amendment | contract §3.3 | 1 | PRD-01 |
| [PRD-02](prd/PRD-02-board-daemon-sqlite-multi-project-board.md) | Daemon, SQLite (all migrations), plug-in contracts, multi-project board, drag-and-drop | PRD-01 | 2 | — |
| [PRD-03](prd/PRD-03-channel-handoff-run-tools-hooks-live-indicator.md) | Channel hand-off, run tools, hooks, live indicator | PRD-02 | 3 | PRD-05, PRD-07 |
| [PRD-05](prd/PRD-05-approval-dialog-spec-editor-git-panel.md) | Approval dialog, spec editor + diff, git panel | PRD-01, PRD-02 | 3 | PRD-03, PRD-07 |
| [PRD-07](prd/PRD-07-tauri-app-shell-and-terminal.md) | Tauri app + terminal (needs Rust) | PRD-02 | 3 | PRD-03, PRD-05 |
| [PRD-04](prd/PRD-04-headless-runner-transcript-and-stop.md) | Headless runner, transcript, Stop | PRD-03 | 4 | — |
| [PRD-08](prd/PRD-08-installer-docs-release.md) | Installer, guard rules, docs, v0.3.0 | all | 5 | — |

Serial hotspots: `server.py` and `tests/test_kanban.py` PRD-01/02 → PRD-03; `routes_runs.py` and `ui/modules/runs.*`
PRD-03 → PRD-04; `plugin.json` PRD-03 (channels) → PRD-08 (version). All migrations in PRD-02. Wave-3 slices extend
the daemon and UI only through the auto-discovered plug-in folders, so they share no files.

## 12. Architect review 1 — disposition
| Finding | Severity | Resolution |
|---|---|---|
| 1 README stage via `set_status`; guard too narrow | Critical | §2 Transition (Q09), `set_status` refuses README, enum validation — PRD-01 |
| 2 shared token lets Claude act as human | Critical | §8 two scoped tokens, guard rules, detection; residual risk accepted (Q12) — PRD-02, PRD-08 |
| 3 hash invalidated by approval line / checkboxes | High | §2 normalisation — PRD-01 |
| 4 kit `sdd` refuses board approval | High | amended by PRD-06 (Q10) via `kanban_approval` |
| 5 headless run's own claim refused | High | `KANBAN_RUN_ID`, idempotent claim — PRD-03, PRD-04 |
| 6 channel capability unknowable | High | parent argv detection + live subscription — PRD-03 |
| 7 plug-in contracts missing | High | §3.1–3.2 — PRD-02; `runs.*` owned PRD-03 → PRD-04 |
| 8 `test_kanban.py` ownership, real daemon in tests | High | serial ownership; `tests/helpers.py` isolation — PRD-02 |
| 9 token can't reach event streams / links | High | fetch streaming with header; viewer as API fetch — PRD-02 |
| 10 XSS → terminal | High | §8 CSP, textContent, capability scope, project ids — PRD-02, PRD-04, PRD-07 |
| 11 inherited env in headless runs | High | §5 step 7 env allow-list — PRD-04 |
| 12–25 | Medium/Low | flock + version replace (12); supersede/requeue/unique index (13); sticky states, abandoned, pid+start time (14); PUT key refusal (15); permission denials (16); FR-03 terminal sessions dropped (Q13, proposed) (17); triggers (Q11) (18); `app/.gitignore`, contract in §3.3 (19); installer explicit-only (20); lock-free git (21); actor literals (22); a11y ACs (23); register tests column, NFR-09 wording (24); README-only approval, `--stop`, hook fast exit, token-free URL (25) |

### Architect review 2
| Finding | Resolution |
|---|---|
| 2 (partial) Read tool not covered by the Bash guard | `permissions.deny` Read rule for `ui.token` — PRD-08 |
| 6 (partial) live sessions unowned | `ctx.live_sessions` + test — PRD-02 |
| N1 chat approval vs scope matrix, no DB owner | `record_approval` + `approve-chat` endpoint (client scope, refused for headless) — PRD-02; PRD-03/05 reuse |
| N2 CSP breaks xterm.js styles | `style-src 'self' 'unsafe-inline'` (scripts stay strict) — PRD-02 |
| N3 Q13 open | owner ruling requested at the approval stop |
| N4 DB deletion drops board approval records | ADR-002 consequence; README note — PRD-08 |

## 13. Risks (residual)
| Risk | Mitigation / owner |
|---|---|
| Channels research preview changes | feature-detected; headless fallback; kit owner re-checks on Claude Code updates |
| Code-tab sessions cannot receive channel events (OQ Q04) | auto headless when no live channel session; "Copy prompt"; app terminal starts channel-enabled CLI sessions |
| Same-user agent forges approval despite scoped tokens | detection badge, guard rules, `sdd` accepts only `board_recorded` approvals; accepted by kit owner (Q12) |
| Headless and interactive work on one checkout | one live run per ticket (DB index); headless only when unclaimed |
| Rust toolchain missing | Python PRDs don't need it; installer prerequisite check |
| Hook overhead | throttled, 1 s timeout, fast exit without daemon |
| `channels` key not yet validated by `claude plugin validate` | first step of PRD-03; if refused, the channel works through `--dangerously-load-development-channels server:kanban` and the key is dropped |
