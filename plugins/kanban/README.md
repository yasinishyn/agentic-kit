# kanban: a tiny progress board for Claude Code

A local board with four columns (To do → In progress → Review → Done):
- **every prompt you type becomes a card** automatically;
- Claude adds steps and child cards over MCP;
- a small web page shows it live.

It works on its own, and it also works with the `sdd` skill: an SDD feature card with one step per stage, plus a card per PRD.

## How it connects to Claude Code

| Piece | What it does |
|---|---|
| `hooks/hooks.json` → `scripts/hook.py` | `UserPromptSubmit`: adds a card for your prompt (In progress) and tells Claude its id. `Stop`: moves it to Review when Claude finishes. Never blocks: errors are swallowed. |
| `.mcp.json` → `scripts/server.py` | A stdio MCP server (JSON-RPC, Python standard library only) with 5 tools: `kanban_list`, `kanban_add`, `kanban_move`, `kanban_update`, `kanban_delete`. In Claude Code they appear as `mcp__plugin_kanban_kanban__<tool>`. |
| `scripts/server.py` (web thread) | Serves the board on `http://127.0.0.1:<port>/`. The port is picked per project and written to `.kanban/url`. |
| `skills/kanban/SKILL.md` | Tells Claude when and how to use the board, including the SDD conventions. |

**Data:**
- **Where it's stored:** `<project>/.kanban/board.json`, one board per project. The project is found through `KANBAN_PROJECT_DIR`, then `CLAUDE_PROJECT_DIR`, then the nearest folder with `.kanban/`, `.claude/` or `.git`.
- **Not committed:** the folder contains a `.gitignore` of `*`, because cards hold your prompt text.
- **Concurrent writers:** they're serialised with a file lock, so several sessions can share a board.

## Install

The repository is a plugin marketplace called `agentic-kit`. You need Python 3 on the PATH.

```bash
claude plugin marketplace add /path/to/agentic-kit      # or owner/repo once it is on GitHub
claude plugin install kanban@agentic-kit
```

Restart Claude Code, or run `/reload-plugins`. Check with `/mcp`: the server is listed as `plugin:kanban:kanban`.

To have a project offer it to everyone, add this to the project's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "agentic-kit": { "source": { "source": "github", "repo": "<owner>/<repo>" } }
  },
  "enabledPlugins": { "kanban@agentic-kit": true }
}
```

## Use

- **Find the board:** ask Claude "where is the board?" (it calls `kanban_list`), or open the URL in `.kanban/url`.
- **Board without Claude:** `python3 <plugin>/scripts/server.py --ui` (Ctrl-C to stop).
- **From the page:** add cards, move them left or right, and delete them. The page refreshes every 2 seconds.

## Settings (environment variables)

| Variable | Effect |
|---|---|
| `KANBAN_PORT` | Fixed port for the web board (default: 8700–8899, derived from the project path) |
| `KANBAN_NO_UI=1` | MCP server only, no web board |
| `KANBAN_DISABLE_PROMPT_CARDS=1` | Don't create a card per prompt |
| `KANBAN_PROJECT_DIR` | Force the project folder |

## Safety

- **Loopback only:** the web board listens on `127.0.0.1`, and requests with a foreign `Host` header are refused (DNS-rebinding protection).
- **No cross-site writes:** state changes need an `X-Kanban` header, which no cross-site form can send.
- **Local only:** nothing leaves your machine.

## Tests

```bash
python3 plugins/kanban/tests/test_kanban.py
claude plugin validate plugins/kanban && claude plugin validate .
```
