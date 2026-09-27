---
name: ticket
description: Use when the user asks for new work that needs a specification - a feature, a change, a migration or port, a significant fix ("new ticket", "spec this", "let's build…", "start work on…", "/kanban:ticket …") - to open a ticket on the board and kick off the SDD flow. Not for one-off questions or trivial edits.
argument-hint: <short title of the work>
---

# Ticket: open a ticket and kick off SDD

A ticket exists when a request will produce a specification. This skill opens one and starts the `sdd` flow.

## Steps
1. **Decide if it's a ticket.**
   - **Yes:** the work needs a spec (a new behaviour, a schema, auth, an external integration, a port, or anything multi-step).
   - **No:** a question or a trivial edit. Answer or do it directly; no ticket.
2. **Name it.**
   - A short neutral title and a folder slug, e.g. "Password reset page" → `password-reset-page`.
   - Check `.SDD/specs/` for an existing ticket first, with `kanban_board` or `ls`. If one exists, continue it instead.
3. **Create it.**
   - With the plugin: `kanban_new_ticket(title, slug, summary)`.
   - Without it: create `.SDD/specs/<slug>/README.md` with this frontmatter
     ```
     ---
     title: <title>
     status: discovery
     updated: <YYYY-MM-DD>
     ---
     ```
     then a one-paragraph neutral goal, and a `## Progress` checklist (Discovery, Architect, Approval, Developer, QA, Demo, E2E).
4. **Kick off SDD.**
   - Invoke the `sdd` skill (or `<plugin>:sdd`, whichever is installed) for this slug, starting at Discovery.
   - The ticket README becomes the spec index: link `01-discovery.md` and later files from it.
5. **Tell the user** the ticket path and the board URL (`kanban_board` shows it), in one line.

## Keep it moving
Follow the `kanban` skill.
- **Stages:** move the ticket as each gate passes.
- **Approval:** stop at Approval for the user's "execute".
- **PRDs:** each PRD file gets `status:` frontmatter, and its acceptance criteria are checkboxes.
- **Done:** only after the hand-off note exists and the E2E evidence is in.
