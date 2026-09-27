# Instructions for Claude Code

<!-- Fill the placeholders below for your project, then delete this comment. -->

## Project
- What it is: <one line about the product and its stack>
- Local environment: <your local environment> (e.g. `docker compose up`), app at <your local app URL>
- Tests: `<your test command>` · E2E: `<your e2e command>` · Seed data: <your seed data>

## Hard rules (enforced by .claude/settings.json + .claude/hooks/guard-bash.sh)
1. Never push, deploy or publish; humans push. Finish work with a hand-off note (.SDD/templates/handoff-note.md).
2. Never touch shared or production databases; local environment only. Synthetic data only; never print secrets.
3. Commit only when the user has asked for execution, and only on feature branches.
4. The repo holds only code, code tooling and SDD artefacts; messages, briefs and Q&A stay in chat.
5. If the guard hook blocks a command, tell the user; never work around it.

## How we work — light ADLC (.SDD/README.md, skill `sdd`)
Discovery → Architect (ADRs, PRDs) → **user approves** → Developer (TDD) → QA → Demo → E2E. Specs live in .SDD/specs/<slug>/.

Skills: `sdd`, `test-driven-development`, `systematic-debugging`, `verification-before-completion`.
Agents: `architect` (design review), `qa-verifier` (evidence-based verification).

## Long-term memory
@.claude/memory/MEMORY.md
