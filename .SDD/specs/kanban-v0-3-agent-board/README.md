---
title: Kanban v0.3: desktop app, agent hand-off on move, live work indicator, kanban:sdd skill
status: e2e
updated: 2026-09-30
---

# Kanban v0.3: desktop app, agent hand-off on move, live work indicator, kanban:sdd skill

Evolve the kanban plugin from a passive board into a local control surface for SDD work: it runs as a standalone desktop app window, moving a ticket to a new stage hands the next stage to Claude through the plugin's MCP server, tickets being worked on show a live in-progress indicator, and a plugin skill `kanban:sdd` specs an issue and writes its spec files as a board ticket.

## Progress
- [x] Discovery
- [x] Architect
- [x] Approval
- [x] Developer
- [x] QA
- [x] Demo
- [ ] E2E

## Files
- [01-discovery.md](01-discovery.md) (Agreed)
- [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md)
- [registers/REQUIREMENT-COVERAGE.csv](registers/REQUIREMENT-COVERAGE.csv)
- [03-architecture.md](03-architecture.md) · [adr/](adr/) · [prd/](prd/) (Draft, waiting for approval)
