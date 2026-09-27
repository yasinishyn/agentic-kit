---
name: kanban
description: Use when the kanban MCP tools (kanban_list, kanban_add, kanban_move, kanban_update) are available and you start, split, progress or finish a piece of work, when running an SDD stage or PRD, or when the user asks about progress or "the board". Explains how prompt cards, child cards and steps are used, and how to find the web board URL.
---

# Kanban: keep the board true

The user watches this board to follow your work. Each user prompt becomes a card automatically:
- the `UserPromptSubmit` hook adds it to **In progress** and tells you its id (e.g. `K-12`);
- the `Stop` hook moves it to **Review** when you finish your turn.

You only add structure and move cards; you never need to create the prompt card yourself.

## Tools
Claude Code shows them as `mcp__plugin_kanban_kanban__<tool>`.

| Tool | Use it to |
|---|---|
| `kanban_list` | See the board and the web URL (tell the user the URL when they ask where the board is) |
| `kanban_add` | Add a card: `title`, `status` (`todo`, `doing`, `review`, `done`), `parent_id` to nest it under a feature |
| `kanban_move` | Move a card between columns |
| `kanban_update` | Rename, set a note, `add_step`, or tick `complete_step` (1-based) |
| `kanban_delete` | Remove a mistaken or duplicate card |

## When to use it
- **Multi-step work** (more than about 3 steps): add the steps to the prompt card with `add_step`, and tick each one as it finishes.
- **Separate work items** (PRDs, parallel streams, follow-ups the user should see): child cards with `parent_id`.
  - `todo` = planned, `doing` = working now, `review` = waiting for the user, `done` = accepted or verified.
- **Before saying something is finished:** move its card to `review`, not `done`. `done` is for work the user accepted, or that passed its gate with evidence (see `verification-before-completion`).
- **Trivial one-shot questions:** leave the prompt card alone; the hooks handle it.

## SDD (when the `sdd` skill is running)
1. At the start of a feature, add one card `SDD: <slug>` in `doing`, with one step per stage in order: Discovery, Architect, Approval, Developer, QA, Demo, E2E, Hand-off. Put the spec path in the note (`.SDD/specs/<slug>/`).
2. Tick each stage's step when its gate passes. At the approval STOP, move the feature card to `review`; move it back to `doing` when the user says "execute".
3. In the Architect stage, add one child card per PRD (`PRD-NN: <title>`, `todo`). Move a PRD card to `doing` when its stream starts, to `review` when its gates are green, and to `done` after QA accepts it.
4. Open questions that block a stage can be child cards in `review` titled `OQ <id>: <short question>`.
5. The main session updates the board at stage boundaries. Subagents and workflow agents don't need to.
6. At hand-off, the feature card goes to `review` with the hand-off note path in its note.

## Rules
- **Short, factual titles.** The board is local and gitignored, but still: no secrets, credentials or personal data in titles, notes or steps.
- **Keep the board truthful:** never move something to `done` without evidence.
- **If the tools fail,** carry on with the work and mention it once. The board must never block real work.
