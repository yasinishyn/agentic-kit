# ADR-006: Approval: recorded human decision, board dialog or chat

| Field | Value |
|---|---|
| Status | Accepted — Decided by kit owner, 2026-09-28 (OQ Q03, Q09, Q10, Q12) |
| Date | 2026-09-28 |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-agent-board |

## Context
Leaving Approval must stay a human decision; the kit rule was "only in the user's own chat message". The owner decided a board confirm dialog also counts, and a skill may move/approve from a chat prompt.

## Options
| Option | For | Against |
|---|---|---|
| **A. Dialog or chat → approval record in README (+hash) and DB snapshot; enforced in `move_ticket` (chosen)** | explicit, reviewable in the commit, enforced for every caller | hash rules to get right |
| B. Column move = approval | fastest | accidental drags execute specs |
| C. Chat only | matches the old rule | the board cannot approve, contrary to Q03 |

## Decision
Frontmatter `approved_by` (`human (board)` or `human (chat)`), `approved_at`, `approved_hash`; the approval line under the `03-architecture.md` header (README only without 03-*). Scope (Q09, amended by Q14 in QA): entering developer/qa/demo/e2e/done from outside that set needs a valid approval; moves inside it are free (badge when no record); in local mode `kanban_approve` writes the markdown record only (Q15); `set_status` cannot write a README stage. The hash ignores volatile keys, approval lines and checkbox state (architecture §2). `kanban_approve` is refused in headless runs and put under `permissions.ask` by the installer. The kit `sdd` skill accepts a board approval verified through `kanban_approval` (`valid` and `board_recorded`), which also opts in to parallel streams (Q10). A README approval without a DB record is flagged, not accepted (Q12).

## Consequences
+ enforced for every writer; reviewable in the commit. − a same-user agent could still forge files; detection and guard rules reduce, the owner accepted the residual risk (Q12).
