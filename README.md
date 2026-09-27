# Agentic kit for Claude Code

A project-neutral starter kit: a light, spec-driven delivery flow (ADLC), disciplined engineering skills, two review
agents and a guard hook that keeps agents away from pushes, deploys, cloud CLIs, shared databases and credentials.

## What's inside
| Path | Purpose |
|---|---|
| `CLAUDE.md` | Project instructions and hard rules (fill the `<your …>` placeholders) |
| `.claude/settings.json` | Deny/ask permission rules + the PreToolUse guard hook |
| `.claude/hooks/` | `guard-bash.sh` → `guard_bash.py` (with a per-project CONFIG block) and its tests |
| `.claude/skills/sdd/` | Discovery → Architect → approval → Developer → QA → Demo → E2E, tiers, parallel streams |
| `.claude/skills/test-driven-development/` | Red-green-refactor, plus guidance on writing good tests |
| `.claude/skills/systematic-debugging/` | Root cause → pattern → hypothesis → fix through a failing test |
| `.claude/skills/verification-before-completion/` | Evidence before any "done" claim |
| `.claude/agents/` | `architect` (read-only design review), `qa-verifier` (independent verification) |
| `.claude/memory/MEMORY.md` | Index for long-term project memory |
| `.SDD/` | The process README, spec templates and `specs/` folder |
| `LICENSES/` | Upstream licences and notices for adapted material |
| `plugins/kanban/` | Optional Claude Code plugin: a tiny local progress board. A card per prompt; Claude updates it over MCP; SDD-aware ([README](plugins/kanban/README.md)) |
| `.claude-plugin/marketplace.json` | Makes this repo a plugin marketplace named `agentic-kit` |

## Install
```bash
./install.sh /path/to/your/project          # refuses to overwrite existing files
./install.sh /path/to/your/project --force  # backs up conflicting files first, then overwrites
```
Then, in the project:
1. Fill the placeholders: `grep -rn '<your ' CLAUDE.md .claude .SDD`.
2. Add project rules (deploy scripts, local DB host names, forbidden env vars) to the CONFIG block in
   `.claude/hooks/guard_bash.py`, add a test case for each, and run `bash .claude/hooks/test_guard_bash.sh`.
3. Review `.claude/settings.json`, then commit the kit on a feature branch.

Requirements: `bash`, `python3` (the hook fails closed without it), `jq` optional.

## Optional: the kanban board plugin
The plugin is installed per user, not copied by `install.sh`:
```bash
claude plugin marketplace add /path/to/agentic-kit   # or <owner>/<repo> once it is on GitHub
claude plugin install kanban@agentic-kit
```
Then restart Claude Code. Every prompt becomes a card on a local web board. The `sdd` skill uses the board automatically
when the kanban tools are available: one feature card with a step per stage, plus one card per PRD. See
[plugins/kanban/README.md](plugins/kanban/README.md).

## Licences
Parts of the skills and agents are adapted from MIT-licensed projects:
[obra/superpowers](https://github.com/obra/superpowers) by Jesse Vincent and
[msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents). See [`LICENSES/NOTICE.md`](LICENSES/NOTICE.md).
