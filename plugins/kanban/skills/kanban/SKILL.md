---
name: kanban
description: Use when a ticket's stage, a PRD or task status, or a checklist item changes during SDD work, when you finish a stage gate, or when the user asks about progress, tickets or "the board". Explains the markdown ticket format under .SDD/specs, the ADLC columns and the kanban MCP tools, and how to find the web board URL.
---

# Kanban: the board is the `.SDD/specs` markdown

The markdown files are the source of truth; the board only displays them. Keep the files true and the board is true.

## Format

| Thing | File | Status field (frontmatter) |
|---|---|---|
| **Ticket** (one piece of work that has a spec) | `.SDD/specs/<slug>/README.md` | `status: discovery \| architect \| approval \| developer \| qa \| demo \| e2e \| done`, which is the board column |
| **Sub-task** | `prd/PRD-NN-*.md` (written by the Architect stage) or `tasks/NN-*.md` | `status: todo \| doing \| blocked \| done` |
| **Sub-sub-task** | a markdown checkbox `- [ ]` / `- [x]` in either file | ticked or not |

- **Ticket README frontmatter:** `title`, `status`, `updated` (date). The README also has a `## Progress` checklist with one box per stage.
- **Not tickets:** folders starting with `_` or `.`.
- **Links:** link other docs with relative markdown links; the board and its file viewer follow them.

## Tools

Claude Code shows them as `mcp__plugin_kanban_kanban__<tool>`; load them with ToolSearch "kanban" if they are deferred.

| Tool | Does |
|---|---|
| `kanban_board` | Lists tickets by stage, with sub-tasks, progress, paths and the web URL |
| `kanban_new_ticket` | Creates `.SDD/specs/<slug>/README.md` in Discovery. Never overwrites. |
| `kanban_move` | Sets the ticket stage and ticks the Progress boxes of earlier stages |
| `kanban_add_subtask` | Creates `tasks/NN-<slug>.md` (status `todo`) with optional checklist items |
| `kanban_set_status` | Sets the status of a PRD or task file |
| `kanban_check` | Ticks or unticks a checkbox, by number or by the start of its label |

**Without the plugin**, edit the frontmatter and checkboxes directly; the result is identical.

## When to update

**Ticket:**
- Move it when a stage **gate passes**:
  - Discovery → Architect when the Discovery exit is met;
  - Architect → **Approval** when the spec is ready for the user;
  - Approval → Developer only after the user says "execute";
  - then QA → Demo → E2E → Done at the hand-off.
- Never move it forward without the gate's evidence (see `verification-before-completion`). Moving back is allowed: say why in the README.

**Sub-tasks:**
- A PRD or task goes to `doing` when its stream starts and to `done` when its gates are green and QA accepts it.
- Use `blocked` plus a one-line reason in the file when it waits on an open question.
- Tick checkboxes (acceptance criteria, tests) as they are proven.

**Who updates:** the main session, at stage boundaries. Subagents don't have to.

## Rules
- **Content:** titles and notes are factual and neutral. No secrets, credentials, personal data or chat transcripts; the files are committed with the code.
- **Scope:** don't create tickets for one-off questions. A ticket exists when work produces a specification (see the `ticket` skill).
- **Failures:** if the tools fail, keep working, edit the markdown directly, and mention it once.
