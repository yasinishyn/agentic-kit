# Agent guard hook (`guard-bash.sh`)

A Claude Code **PreToolUse** hook on the `Bash` tool, wired in the committed `.claude/settings.json`, so every developer
who runs Claude Code in the project gets it. It complements the `permissions.deny` rules (prefix rules can be
bypassed with `git -C x push`, `bash -c "…"` etc.; the hook parses the command).

**Fails closed:** if `python3` is missing or the checker errors, the command is blocked (exit 2).

## What it blocks (humans do these, never agents)
| Rule | Examples |
|---|---|
| Any `git push` | `git push`, `git -C dir push`, `bash -c 'git push'`, `eval git push` |
| Anything under a `deploy/` directory | `./deploy/release.sh`, `bash deploy/*.sh`, `python deploy/x.py` |
| The `aws` CLI | `aws s3 ls`, `AWS_PROFILE=x aws secretsmanager …` (use a local emulator in <your local environment>) |
| GitHub PR/release/repo writes | `gh pr create`, `gh pr merge`, `gh release create` |
| Non-local DB clients | `psql -h remote…`, `psql postgresql://…@remote/…`, `pg_dump --host=remote` |
| Credential files | `cat ~/.aws/credentials`, `cat ~/.ssh/…`, `source .env`, `cat .env.production` (`.env.example`/`.template`/`.sample` are fine) |

## Project-specific rules (CONFIG block in `guard_bash.py`)
All lists are empty by default and additive (they can only block more):

| List | Use it for |
|---|---|
| `EXTRA_LOCAL_HOSTS` | docker-compose service names that count as local DB hosts |
| `FORBIDDEN_ENV_VARS` | secrets wiring for shared/production systems agents must never assign or export |
| `FORBIDDEN_EXECUTABLES` | <your deploy scripts> or release tools that live outside `deploy/` |
| `DB_WRITING_SCRIPTS` | seed/load/migrate scripts that read `DATABASE_URL` from `.env` (blocked on the host unless an inline local URL is given) |
| `EXTRA_SECRET_PATHS` | extra secret files (`secrets/`, `*.pem`, …) |

Known, intentional false positive: a heredoc whose *text* contains a blocked command (e.g. writing a doc that shows
`git push`) is blocked. Use the Write tool to create such files.

## Tests
```bash
bash .claude/hooks/test_guard_bash.sh   # must print failed=0
```

## Changing the policy
Edit `guard_bash.py`, add a case to `test_guard_bash.sh`, run the tests, and have a human review the diff.
The hook is a guardrail, not a sandbox: an agent determined to misbehave via e.g. `python -c` subprocesses is out of
scope — agents are instructed (CLAUDE.md) and reviewed by humans.
