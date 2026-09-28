# Writing specs: discovery questions, ADRs, PRDs and the execution map

Load this when writing `01-discovery.md`, `OPEN-QUESTIONS.md`, ADRs, PRDs or the execution map in
`03-architecture.md`. It is about the *quality* of each artefact; [stages.md](stages.md) says *when* to write it.

## Discovery: what, not how

- A requirement describes what a user or system **experiences**, never how it is built. "Clients that reconnect catch
  up on missed events" is a requirement; "a ring buffer with 1000 slots" is an architecture decision for later.
- **Gap check.** Before closing Discovery, walk these categories for every feature and list what is missing:
  | Category | Ask yourself |
  |---|---|
  | Context | Problem, who it is for, what is in and out of scope |
  | Functional | Workflows, business rules, data in and out, integrations |
  | Non-functional | Performance, security, accessibility, availability, audit, determinism |
  | Constraints | Systems or contracts that cannot change; tech limits |
  | Success criteria | Acceptance criteria and **testable** metrics (a load time, a count, an error rate — never an uncontrollable business outcome such as "revenue grows") |
- **Research** (web, docs, the codebase) is for asking sharper questions, never for answering questions only the
  user or a domain owner can answer.
- **Partial readiness is fine.** Architect can start on the parts that are clear while other questions stay open,
  as long as no open question blocks the core design (the stage entry criterion).

## Questions that deserve an answer

Each question in `OPEN-QUESTIONS.md` or AskUserQuestion is:
- **specific** — "What response time is acceptable for a search over 10M records?", not "How fast should it be?";
- **context-aware** — it names what was already said or found ("The export already writes CSV at `exporter.py:40`;
  should the new report reuse it?");
- **actionable** — it offers options and a recommended one when the decision is not a domain ruling;
- **new** — never ask what the request, the code or an earlier answer already settles.
Answered rows stay in the register with their decision; never delete them.

## ADRs

- **Laconic:** Context 1–3 sentences, each option with what it gives up, Decision 1–3 sentences naming the accepted
  trade-off, Consequences as short bullets including the tests that pin the decision.
- **Specific:** name the exact technology or mechanism ("SQLite in WAL mode", not "a local database").
- **Simplicity first.** Decision order: can the existing stack do it? → does an accepted ADR already cover it? → only
  then a new technology, and the ADR says why the existing stack cannot.
- **Extend, don't conflict.** Read the project's existing ADRs first. A new ADR that contradicts an accepted one must
  say `Supersedes ADR-NNN` (and that ADR becomes `Superseded by …`); silent contradictions are review findings.
- Durable decisions live in ADRs, not buried in PRDs.

## PRDs

- **What, not how.** Prose over code. Signatures and data shapes are fine; function bodies are not (at most a
  3–5 line snippet when prose genuinely fails, e.g. branching logic with 3+ cases).
- **Implementation target:** 1–2 sentences stating the deliverable. The why is already in the discovery.
- **Steps ordered by dependency**, each independently checkable, with files marked create/modify.
- **Interfaces:** inputs with validation rules, outputs with their consumers, and every error state.
- **Acceptance criteria are testable.** "Login works" is not; "a user with valid credentials receives a session and
  lands on the dashboard" is. Non-functional criteria carry a measurable threshold.
- **Edge cases say how they are handled**, not just that they exist, with a priority: must (this PRD), should,
  could (later). "Validate the email" is incomplete: what happens when it is empty, malformed or already registered?
- **Out of scope** is explicit, naming the later PRD when known.
- **Gaps stop the PRD.** If a missing answer prevents concrete steps (a default value, a timeout, user-facing wording,
  an integration parameter), do not guess and do not write that PRD: add the question to `OPEN-QUESTIONS.md` and mark
  the PRD `blocked`. Minor gaps become a written assumption in the PRD.

## Execution map (in `03-architecture.md`)

The PRD table in `03-architecture.md` is the execution map. For every PRD it records:
- **Wave** (phase): PRDs in the same wave may run in parallel; a later wave starts only when its predecessors are done.
- **Requires:** the PRDs it depends on. Each edge must be real, there are no cycles, and no PRD requires one in a later
  wave.
- **Owns:** its touch-set — files, plus shared artefacts it alone may change (migrations, route map, permission
  registry, shared fixtures, a shared doc or ADR). Within a wave, `Owns:` sets are **disjoint**; a shared artefact is
  either given to one PRD or sequenced across waves.
- **Review verdict:** the **architect** agent checks the map (flow correct? any same-wave ownership clash?) and the
  result is written under the table: `no blocking conflicts`, or `REVISED (n) — conflict-free` after fixes. Developer
  does not dispatch a wave against a map without a verdict.
