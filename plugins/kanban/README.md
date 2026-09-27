# kanban: a board over your `.SDD/specs` markdown

The board is a **view over the spec files**; the markdown is the source of truth.
- **A ticket is a spec folder**, and its **columns are the ADLC stages**.
- **Status lives in frontmatter**, so the developer commits it and reviews it with the code, and it works without the plugin too.
- **Every markdown file is one click away** on the board.

```
.SDD/specs/password-reset/                 ← ticket (a card on the board)
├── README.md        status: developer     ← column; "## Progress" checkboxes = one per stage
├── 01-discovery.md  …                     ← listed under the card's files, opens in the viewer
├── prd/PRD-01-reset-token.md  status: doing   ← sub-task; its checkboxes = sub-sub-tasks
└── tasks/01-write-the-demo.md status: todo    ← extra sub-task (optional)
```

| Level | Where | Values |
|---|---|---|
| Ticket stage (column) | `README.md` frontmatter `status:` | `discovery` · `architect` · `approval` · `developer` · `qa` · `demo` · `e2e` · `done` |
| Sub-task | `prd/*.md`, `tasks/*.md` frontmatter `status:` | `todo` · `doing` · `blocked` · `done` |
| Sub-sub-task | `- [ ]` / `- [x]` checkboxes in any of those files | ticked or not |

Frontmatter looks like this; everything else in the file is yours:

```markdown
---
title: Password reset page
status: developer
updated: 2026-09-27
---
```

Folders whose names start with `_` or `.` aren't tickets. A README without `status:` shows in Discovery with a warning.

## What the plugin adds

| Piece | Does |
|---|---|
| **MCP server** (`scripts/server.py`, stdio, Python standard library only) | Tools for Claude: `kanban_board`, `kanban_new_ticket`, `kanban_move`, `kanban_add_subtask`, `kanban_set_status`, `kanban_check`. In Claude Code they're `mcp__plugin_kanban_kanban__<tool>`. They only edit frontmatter and checkboxes, or create new files; they never delete anything. |
| **Web board** (same process) | `http://127.0.0.1:<port>/`, with the URL written to `.kanban/url`. <br>• **Columns:** the 8 stages. <br>• **Cards:** a progress bar, sub-tasks with a status selector, all of the ticket's `.md` files, and move buttons. <br>• **Viewer:** renders markdown, including tables, checkboxes and relative links, plus an **Open in editor** link. |
| **Skill `ticket`** | Kicks off SDD. When a request will produce a spec, it creates the ticket (Discovery) and starts the `sdd` skill. Run it as `/kanban:ticket <title>`, or just ask to "spec" something. |
| **Skill `kanban`** | Tells Claude when to move tickets and update sub-tasks and checkboxes: at stage gates, never ahead of the evidence. |

There are no hooks: a prompt becomes a ticket only when it produces a specification.

## Install

```bash
claude plugin marketplace add yasinishyn/agentic-kit      # or a local path to the kit
claude plugin install kanban@agentic-kit
```

Restart Claude Code, or run `/reload-plugins`. `/mcp` then lists `plugin:kanban:kanban`. Ask Claude "where is the board?" for the URL.

**Board without Claude:** run `python3 <plugin>/scripts/server.py --ui` from the project folder, or set `KANBAN_PROJECT_DIR`.

## Settings (environment variables)

| Variable | Effect |
|---|---|
| `KANBAN_PORT` | Fixed port (default: 8700–8899, derived from the project path; the next free port if taken) |
| `KANBAN_NO_UI=1` | MCP tools only, no web board |
| `KANBAN_PROJECT_DIR` | Force the project folder. Otherwise: `CLAUDE_PROJECT_DIR`, then the nearest folder with `.SDD/`, `.claude/` or `.git` |
| `KANBAN_EDITOR_URL` | "Open in editor" link template, default `vscode://file/{path}`. For Cursor: `cursor://file/{path}` |

## Safety

- **Loopback only:** the board listens on `127.0.0.1`, and a foreign `Host` header is refused (DNS rebinding).
- **No cross-site writes:** writes need an `X-Kanban` header, which no cross-site form can send.
- **Confined files:** the viewer and the tools only touch files under `.SDD/specs/`.
- **Local only:** nothing leaves your machine. `.kanban/` holds only the URL, and ignores itself in git.

## Tests

```bash
python3 plugins/kanban/tests/test_kanban.py
claude plugin validate plugins/kanban && claude plugin validate .
```
