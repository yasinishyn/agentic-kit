# .SDD — Spec-Driven Delivery (light ADLC)

Every non-trivial feature gets a folder in `.SDD/specs/<slug>/`. The spec is versioned **with the code it
produces** and pushed together by a human. Agents never push, deploy, or apply migrations to shared databases
(see `CLAUDE.md` and `.claude/settings.json`).

Skill: `sdd` (`.claude/skills/sdd/`) runs these stages. Templates live in `.SDD/templates/`.

## The six stages

| # | Stage | Input → output (in `.SDD/specs/<slug>/`) | Gate to leave the stage |
|---|---|---|---|
| 1 | **Discovery** | user request → `01-discovery.md` (neutral goal, scope in/out, FR/NFR, assumptions) + `OPEN-QUESTIONS.md` register. Replacing an existing system → also `02-legacy-analysis.md` | Open questions listed; scope agreed |
| 2 | **Architect** | → `03-architecture.md` (DDD: bounded context, domain model, integration points, reuse map), `adr/ADR-NNN-*.md`, `prd/PRD-NN-*.md` (implementable slices with acceptance criteria, test list, owned files, dependencies) | **User approves the spec and says "execute".** Nothing below starts without it |
| 3 | **Developer** | PRDs → code + unit/integration tests (TDD). Independent PRDs may run in parallel (see "Parallel work") | PRD gates green in `<your local environment>` |
| 4 | **QA** | tests → bugs → fixes → `05-qa-report.md` (full test suite, `/code-review`, `/security-review` + localhost abuse checklist, independent `qa-verifier` agent) | No open Critical/High findings |
| 5 | **Demo** | Claude drives the running app in the built-in browser as seeded test users → `06-demo.md` with screenshots | User reviews the demo |
| 6 | **E2E** | The project's own automation (`<your e2e command>`) against localhost → `07-e2e-report.md` | Green run; any external writes done by a human |

Finish with `handoff-note.md`: branch, commits, test evidence, residual risks, and the exact commands **the user**
runs to push.

## Tiering — keep it light

| Change | Stages |
|---|---|
| Trivial (≤2 files, no critical/schema/auth/integration surface) | Developer → QA (no spec folder needed; note it in the commit message) |
| Feature-lite (one module, no critical/schema/auth impact) | 1-page `01-discovery.md` → Developer → QA → Demo if UI |
| **Business-critical logic, system replacement, migration, auth/permissions, external integration** | All six stages |

## Parallel work (built-in Claude Code capabilities only)

- **Precondition:** the spec folder, `.claude/` and `CLAUDE.md` are **committed** on the feature branch (worktrees contain only committed files).
- Launch independent PRDs with the built-in **Workflow** tool (`agent(brief, {isolation: 'worktree'})`) or the **Agent** tool.
- Each brief is self-contained: PRD path, owned files (disjoint across streams), forbidden files, gates, and **absolute paths in every command** (Bash working directory resets between calls).
- Demo, E2E and database-schema changes run **serially in the main checkout**. Per-stream ports/DBs come from `<your local environment>`.
- After each wave: check each stream's `git diff --name-only` ⊆ its owned files, merge locally, run full gates.

## Folder layout per spec

```
.SDD/specs/<slug>/
  01-discovery.md          02-legacy-analysis.md (optional)            03-architecture.md
  adr/ADR-NNN-<title>.md   prd/PRD-NN-<title>.md                       OPEN-QUESTIONS.md
  05-qa-report.md          06-demo.md   07-e2e-report.md               handoff-note.md
  registers/REQUIREMENT-COVERAGE.csv   test-vectors/…
```

## Rules of thumb
- Requirements get IDs (`<SLUG>-FR-NN`, `<SLUG>-NFR-NN`); PRDs, tests and evidence reference them in `registers/REQUIREMENT-COVERAGE.csv`.
- Domain rulings (thresholds, pricing, eligibility, legal or regulated wording, routing) are **never** decided by an
  agent: record them as open questions / Proposed ADRs for the domain owner.
- Use synthetic data only (from `<your seed data>`). Never real personal data, never shared or production databases.
- Specs hold only SDD artefacts. `OPEN-QUESTIONS.md` is a register (#, question, owner, status, decision / next step;
  template `open-questions.md`). A decision goes in its OQ row and the ADR/PRD it changes ("Decided by <role>,
  <date>"). Messages, call briefs, Q&A logs and e-mail content stay in chat.
