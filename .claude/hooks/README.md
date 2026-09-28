# Agent guard hook (`guard-bash.sh`)

A Claude Code **PreToolUse** hook on the `Bash` tool, wired in the committed `.claude/settings.json`, so every developer
who runs Claude Code in the project gets it. It complements the `permissions.deny` rules (prefix rules can be
bypassed with `git -C x push`, `bash -c "…"` etc.; the hook parses the command).

**Fails closed:** if `python3` is missing or the checker errors, the command is blocked (exit 2).

## What it blocks (humans do these, never agents)
| Rule | Examples |
|---|---|
| Commit-creating git, by default (see "Git policy") | `git commit`, `git push`, `git merge`, `git rebase`, `git cherry-pick`, `git revert`, `git am`, `git pull`, in any form: `git -C dir push`, `bash -c 'git commit …'`, `eval git push` |
| Changing the git identity (always) | `git config user.name …`, `git config --global user.email …` |
| A Claude/AI author, committer or co-author (always, also when commits are allowed) | `--author='Claude <…>'`, `GIT_AUTHOR_NAME=Claude`, a `Co-Authored-By: Claude` trailer, `noreply@anthropic.com` |
| Anything under a `deploy/` directory | `./deploy/release.sh`, `bash deploy/*.sh`, `python deploy/x.py` |
| The `aws` CLI | `aws s3 ls`, `AWS_PROFILE=x aws secretsmanager …` (use a local emulator in <your local environment>) |
| GitHub PR/release/repo writes | `gh pr create`, `gh pr merge`, `gh release create` |
| Non-local DB clients | `psql -h remote…`, `psql postgresql://…@remote/…`, `pg_dump --host=remote` |
| Credential files | `cat ~/.aws/credentials`, `cat ~/.ssh/…`, `source .env`, `cat .env.production` (`.env.example`/`.template`/`.sample` are fine) |
| The kanban board's UI token (human-only) | Any command naming `…/Kanban/ui.token`, `$KANBAN_HOME/ui.token` or `…/kanban/ui.token` (`cat`, `head`, `python3 -c "open(…)"`, `$(cat …)`), or `cd` into a Kanban folder plus `ui.token` |
| Human-only board endpoints | A `127.0.0.1`/`localhost` URL whose path matches `/api/.*(approve\|move\|files?\|runs)`, from `curl`, `wget`, `http` (httpie), `python -c` with `urllib`/`requests` …; read-only calls such as `/api/health` stay allowed. Claude uses the kanban MCP tools instead |

The hook sees only Bash. For the Read tool, the installer also adds `Read(~/Library/Application Support/Kanban/ui.token)`
to `permissions.deny`, and `mcp__plugin_kanban_kanban__kanban_approve` to `permissions.ask` (approving a spec from chat
always asks you).

## Project-specific rules (CONFIG block in `guard_bash.py`)
All lists are empty by default and additive (they can only block more):

| List | Use it for |
|---|---|
| `EXTRA_LOCAL_HOSTS` | docker-compose service names that count as local DB hosts |
| `FORBIDDEN_ENV_VARS` | secrets wiring for shared/production systems agents must never assign or export |
| `FORBIDDEN_EXECUTABLES` | <your deploy scripts> or release tools that live outside `deploy/` |
| `DB_WRITING_SCRIPTS` | seed/load/migrate scripts that read `DATABASE_URL` from `.env` (blocked on the host unless an inline local URL is given) |
| `EXTRA_SECRET_PATHS` | extra secret files (`secrets/`, `*.pem`, …) |

## Git policy: `ALLOW_AGENT_COMMITS`

`ALLOW_AGENT_COMMITS` in the CONFIG block of `guard_bash.py` (function `git_reason`) sets who commits.

| | `False` (default) | `True` (opt-in) |
|---|---|---|
| `git add`, read-only git (`status`, `diff`, `log`, `show`, `rev-parse`) | allowed | allowed |
| `commit`, `push`, `merge`, `rebase`, `cherry-pick`, `revert`, `am`, `pull` | **blocked**: the developer runs them from the hand-off's "Git — for the developer" block | allowed, as the developer's own identity |
| Claude/AI author, committer or `Co-Authored-By` trailer | blocked | **blocked** |
| `git config user.name` / `user.email` | blocked | **blocked** |

**To opt in** (a team decision; have a human review the diff):
1. Set `ALLOW_AGENT_COMMITS: bool = True` in `guard_bash.py`.
2. Remove `"Bash(git push *)"` from `permissions.deny` in `.claude/settings.json` (it is a prefix rule the hook cannot
   override). Keep `"attribution": {"commit": "", "pr": ""}` there, so Claude Code adds no Claude trailer or PR line.
3. Make sure each developer's own `user.name`/`user.email` is configured; commits are made under it.
4. Update `test_guard_bash.sh` (below) and run it.

Deploy scripts, the `aws` CLI, `gh pr create/merge` and the other rules stay blocked either way.

Known, intentional false positive: a heredoc whose *text* contains a blocked command (e.g. writing a doc that shows
`git push`) is blocked. Use the Write tool to create such files.

## Tests
```bash
bash .claude/hooks/test_guard_bash.sh   # must print failed=0
```
The git cases (the `git push` rows at the top and the `# --- git: stage yes, commit/push no …` block) assume the
default (`ALLOW_AGENT_COMMITS = False`). After opting in, change the expected code of every commit/push/merge/pull case
from `2` to `0`, keep the identity case at `2`, and add cases such as:
```bash
check 0 "commit as the developer"   "git commit -m 'PRD-01: add rules (X-FR-01)'"
check 2 "Claude co-author trailer"  "git commit -m x -m 'Co-Authored-By: Claude <noreply@anthropic.com>'"
check 2 "Claude author"             "git commit --author='Claude <noreply@anthropic.com>' -m x"
check 2 "AI identity via env"       "GIT_AUTHOR_NAME=Claude git commit -m x"
check 2 "change git identity"       "git config user.name 'Claude'"
```
To probe a single command by hand (exit 0 = allowed, 2 = blocked; the reason is printed on stderr):
```bash
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | .claude/hooks/guard-bash.sh; echo "exit=$?"
```

## Changing the policy
Edit `guard_bash.py`, add a case to `test_guard_bash.sh`, run the tests, and have a human review the diff.
The hook is a guardrail, not a sandbox: an agent determined to misbehave via e.g. `python -c` subprocesses is out of
scope — agents are instructed (CLAUDE.md) and reviewed by humans.
