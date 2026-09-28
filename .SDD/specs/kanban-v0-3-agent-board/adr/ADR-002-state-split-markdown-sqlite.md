# ADR-002: State split: markdown is the source of truth, SQLite holds operational state

| Field | Value |
|---|---|
| Status | Accepted — Decided by kit owner, 2026-09-28 (OQ Q03 note) |
| Date | 2026-09-28 |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-agent-board |

## Context
The app needs an embedded DB, but specs must stay in the project folder — the main difference from paperclip, which keeps issues in Postgres.

## Options
| Option | For | Against |
|---|---|---|
| **A. Markdown SoT + per-user SQLite for operational data (chosen)** | specs reviewable and committed with code; DB deletable without data loss; one DB for all projects | two stores to keep consistent |
| B. Everything in `.kanban/*.json` per project | no DB | no transactions for claims; runs across projects hard to list; file churn |
| C. Everything in SQLite (paperclip) | one store, rich queries | specs leave git; breaks the kit's SDD contract |

## Decision
SQLite at `<KANBAN_HOME>/kanban.db` (WAL, `PRAGMA user_version` migrations, 0600). Tables: `projects`, `sessions`, `handoffs`, `runs`, `run_events`, `approvals`, `approval_files`, `ticket_sessions` (claude session id per ticket), `settings`. Terminal sessions are not persisted; they live in the Tauri process (Q13, proposed). Ticket status, approval record and checkboxes are written to markdown first; the DB stores only facts about process and history. Board reads always come from markdown (with an mtime cache).

## Consequences
+ deleting the DB loses history only. − approvals exist twice (README for the commit, DB for the diff snapshot and `board_recorded`); deleting the DB drops board approval records, so tickets in execution show "approval not recorded" until re-approved.
