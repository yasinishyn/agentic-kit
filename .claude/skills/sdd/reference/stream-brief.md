# Stream brief: template

Use this template for any subagent that writes code, whether launched by an Agent call, by `streams.workflow.js` (which
embeds the essentials and points here for the Rules), or by a Workflow you write by hand. The receiving agent **has not
seen the conversation**, so the brief must be self-contained. Fill every `<…>`. Every path is absolute.

---

````markdown
# Stream <id> — <PRD-NN>: <title>

| Field | Value |
|---|---|
| Checkout (all writes here, owned files only) | <WT> = /abs/path/to/repo (shared with other streams; opt-in variant: your own worktree) |
| Branch | <the developer's current branch> (don't change it; opt-in variant: <featureBranch>--<id>) |
| Base | <branch> @ <sha> |
| PRD | <WT>/.SDD/specs/<slug>/prd/PRD-NN-<title>.md |
| ADRs | <WT>/.SDD/specs/<slug>/adr/ADR-NNN-….md, … |
| Requirements | <SLUG>-FR-.., <SLUG>-NFR-.. |
| Stream resources (if any) | app http://<id>.localhost:<port> · local DB <name> · cache <index/prefix> |

## Goal
<1–3 sentences copied from the PRD.>

## Acceptance criteria (verbatim from the PRD)
- [ ] …

## Owned files (the ONLY paths you may create or change)
- `src/…`
- `tests/…`

## Forbidden
- Everything else, in particular: `.SDD/specs/<slug>/**` (orchestrator-owned: registers, OPEN-QUESTIONS, reports),
  `migrations/` (unless listed above), hotspots owned by another PRD: <list>, every file another stream owns.

## Interfaces you must honour
<Signatures and contracts other streams depend on, e.g. `evaluate_rules(input, *, today) -> Result` fields; or "none".>

## Tests to write first (from the PRD)
| Test (file::name) | Asserts (literal expected values) |
|---|---|

## Gates (run exactly; Bash cwd resets between calls)
```bash
cd <WT> && <your test command> <paths>
```

## Report (your final message; JSON if a schema is given)
status DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT · filesChanged
(`git -C <WT> status --porcelain --untracked-files=all -- <owned paths>`) · testsAdded · testsSummary (verbatim test
summary line) · suggestedCommitMessage (`PRD-NN: <summary> (<FR ids>)`) · blockers (exact questions) · notes
(deviations, follow-ups). Opt-in variant: also worktree · branch · headSha · commits (oneline).
````

---

## Rules (canonical; the workflow prompt refers to this section)

1. **Stay in your lane.** Change only the owned files. If you need any other change, stop and report it as a blocker.
   Don't work around it.
2. **Absolute paths everywhere.** Use `git -C <WT> …` and absolute paths; `cd` does not persist between Bash calls.
   Other streams write in the same checkout: never edit, revert, format or delete a file you don't own.
3. **TDD.** Follow the `test-driven-development` skill: tests first; watch them fail for the right reason; minimal
   code; refactor only while green. Expected values are literals from the PRD or test vectors, never recomputed by the
   code under test.
4. **Debugging.** On any failure, follow the `systematic-debugging` skill: reproduce, find the root cause, one change at
   a time.
5. **Security.** Output is escaped by default; never bypass the template engine's escaping with user data. Derived or
   business-critical values are recomputed on the server; never trust a posted or hidden field for them.
6. **Local only.** Run gates against local resources. Never use shared, staging or production services, never read
   `.env` or cloud credentials. The guard hook blocks the obvious cases; don't rely on it.
7. **Synthetic data only.**
8. **Git: read-only.** `status`, `diff`, `log`, `show` only. Don't `git add` (the index is shared; the orchestrator
   stages), and never commit, stash, checkout, switch, reset, branch or create worktrees; the guard hook blocks
   commits. Propose the commit message in your report; the developer commits.
   *Opt-in variant* (project set `ALLOW_AGENT_COMMITS`): commit your owned files, explicit paths, on your stream branch
   in your own worktree, as the developer's git identity (no Claude/AI author or co-author trailer); never `git add -A`,
   `--force`, rebase, amend or push. Leave nothing uncommitted.
9. **Evidence.** The report quotes command output verbatim. "Should pass" or "probably" is not evidence.
10. **Build the PRD, nothing more.** No extra features, no refactoring beyond the need, no new dependency an ADR
    didn't sanction, no edge case skipped. If implementation reveals a new decision, report it (or update the ADR if
    you own it).
11. **No sub-orchestration.** Don't spawn further agents or workflows and don't ask the user questions. Return
    `BLOCKED` with the exact question instead.

## Stop conditions → return `BLOCKED` (or `NEEDS_CONTEXT`)

- **A domain ruling is needed** that the spec leaves open (thresholds, pricing, eligibility, regulated wording,
  routing, divergence from existing behaviour or test vectors).
- **A change is needed outside the owned files**, or to an interface another stream relies on
  (`ownership-conflict`).
- **The PRD contradicts an ADR** (`inconsistency`), **can't be built on this stack** (`technical-impossibility`),
  **needs a library or service no ADR sanctions** (`missing-dependency`), or **asks for more than the discovery
  agreed** (`scope-creep`). Name the type in the report.
- **3 fix attempts have failed** on the same problem.
- **The environment isn't usable:** resources missing, a service down, a port taken, or the base lacks the PRD.
- **The PRD is ambiguous or contradicts an ADR.** Quote both.

## Orchestrator checklist before sending a brief

- [ ] The PRD is approved, `Parallelisable: Yes`, and its dependencies are done (earlier wave, gates green).
- [ ] None of its owned files is dirty before the wave (`git status --porcelain --untracked-files=all -- <owned>`).
- [ ] Opt-in variant only: the spec folder and `.claude/` are committed at `<sha>`, and the worktree was created from
      that commit.
- [ ] Owned files are disjoint from every other stream in this wave (matrix in [parallel-work.md](parallel-work.md)).
- [ ] Stream resources are provisioned, and gate commands carry this stream's values (`{WT}` placeholders are allowed
      in `streams.workflow.js` gates).
- [ ] The brief contains no secret values. Credentials are referenced by where they live, never pasted.
