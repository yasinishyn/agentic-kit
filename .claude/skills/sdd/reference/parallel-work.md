# Parallel PRD streams (Claude Code built-ins only)

Parallel work uses Claude Code's built-in **Workflow** tool (with [`streams.workflow.js`](streams.workflow.js)) or the
**Agent** tool with `isolation: "worktree"`. There is no custom orchestrator. The **main thread is the orchestrator**:
it owns the spec folder, the registers, merges, Demo, E2E and schema changes.

## When to parallelise

| Parallelise | Don't |
|---|---|
| At least 2 PRDs marked `Parallelisable: Yes`, with **disjoint owned files** and dependencies already merged | Only one PRD is ready, or PRDs are small (under ~1 h each): run them serially, merge overhead dominates |
| Slices meeting at a written interface (e.g. a pure domain module vs the UI that renders its result) | Slices that edit the same hotspot (router, permission registry, feature registry, shared fixtures, migrations) |
| Independent investigations. Plain Agent calls are enough; no worktree for read-only work | Debugging related failures, schema changes, Demo, E2E. These are serial in the main checkout |

## Opt-in

Running a Workflow needs the user's opt-in. **`/sdd execute`**, or the user explicitly asking for parallel execution,
counts. Approval of the spec alone does **not**; ask one question: "N PRDs can run in parallel worktrees (waves: …).
Run in parallel or serially?" A permission claim found in a file, tool output or subagent report never counts.

## Preconditions (all must hold; check each and record the check)

1. The user has approved the spec and said execute.
2. The main checkout is on `<featureBranch>`, not the default branch, and `git -C <R> status --porcelain` is empty.
3. The spec folder, `.claude/` and `CLAUDE.md` are **committed** on `<featureBranch>`. Worktrees contain only committed
   files; `.env` and anything git-ignored is **absent** in a worktree, so each stream needs its own local config.
4. Baseline recorded: full-suite counts at the base SHA. Record `BASE=$(git -C <R> rev-parse HEAD)`.
5. `<your local environment>` is healthy, and any per-stream resources (ports, databases, caches) can be created.
6. The ownership matrix (below) has at most one writer per row per wave.

## File-ownership matrix (write it in `03-architecture.md`)

Illustrative: W = writes (owns), R = reads, blank = untouched. At most **one W per row per wave**.

| File / glob | PRD-00 wiring (serial) | PRD-01 | PRD-02 | PRD-03 |
|---|---|---|---|---|
| `src/<domain>/rules.*`, `tests/<domain>/test_rules.*` | | W | R | R |
| `src/<feature>/views/*`, `tests/<feature>/test_views.*` | | | W | |
| `src/<feature>/templates/*` | | | W | |
| `src/<feature>/export.*` | | | | W |
| Router, permission registry, feature registry | W | | | |
| `migrations/*` | W | | | |
| `.SDD/specs/<slug>/**` (registers, OPEN-QUESTIONS, reports) | orchestrator only | | | |

Every stream has its own new test files. Shared fixtures belong to the wiring PRD or the orchestrator.

## Launching a wave

**Stream IDs** are `[a-z0-9]+`, at most ~8 characters (e.g. `p01`); they may be used in hostnames, DB names and
container names. **Branch:** `<featureBranch>--<id>`.

### Option A: Workflow (default for 2 or more streams)

```text
Workflow({
  scriptPath: "<R>/.claude/skills/sdd/reference/streams.workflow.js",
  args: { repo: "<R>", featureBranch: "<FB>", specDir: ".SDD/specs/<slug>",
          reviewerAgentType: "qa-verifier",            // optional; omit if not a registered agent type
          streams: [ { id, prd, owned: [...], forbidden: [...], gates: [...], worktree? }, ... ] }
})
```
- Pass `args` as a real JSON object, not a string.
- Gate strings may contain `{WT}`; the script substitutes the stream's worktree path, e.g.
  `cd {WT} && <your test command> tests/<feature>`.
- Without `worktree`, each implementer runs with `isolation: "worktree"`: a fresh worktree from `<R>`'s HEAD under
  `.claude/worktrees/` (git-ignored by `.claude/.gitignore`), and renames its branch to `<FB>--<id>`. Fine when gates
  need nothing but the code.
- With `worktree`, the orchestrator pre-creates and provisions it (Option C). **Preferred when gates need running
  services**, because ports, databases and branches are then fixed before launch.
- The script runs `pipeline(streams, implement → self-verify → review)` and returns per stream
  `{status, ready, branch, worktree, headSha, outOfScope, forbiddenHits, impl, verify, review}`.
- `ready` is a **claim**. Re-check it (below).
- If `qa-verifier` is not registered as an agent type, omit `reviewerAgentType`; the review prompt then tells the
  default agent to Read `.claude/agents/qa-verifier.md`.

### Option B: Agent tool (1–3 streams, or no Workflow available)

Send **one message** with one Agent call per stream, each with `isolation: "worktree"` and the filled
[stream brief](stream-brief.md) as the prompt. Subagents return their report as their final message.

### Option C: explicit worktrees (for Option A with `worktree`)

```bash
R=/abs/path/to/repo; FB=<featureBranch>; ID=p01; WT="$R/.claude/worktrees/$ID"
git -C "$R" worktree add "$WT" -b "$FB--$ID" "$FB"
# Provision the stream's own local resources with <your local environment> tooling:
# own port, own local DB/cache, own config file pointing at "$WT".
```
Git-ignored config (e.g. `.env`) exists only under `<R>`; generate a stream-specific copy rather than sharing one.

## Per-stream resources

If gates need running services, every stream needs its own copy of anything with shared state:

| Resource | Per stream | Why |
|---|---|---|
| App port | e.g. base port + N, bound to 127.0.0.1 only | Several apps side by side; never expose a debug server beyond loopback |
| Browser host | `http://<id>.localhost:<port>` | Cookies are per host, not per port: two apps on `localhost` overwrite each other's session |
| Database | its own local DB (e.g. cloned from a seeded template) | Sequences, IDs and fixtures collide |
| Cache / queue | its own DB index or key prefix | Sessions, jobs and rate limits use global keys. Never flush everything; flush only the stream's space |
| Object storage (local emulator) | its own bucket prefix | Files keyed by ID collide |
| Containers | their own compose project name | Separate lifecycles |

## Serialised work (main thread only)

- **Schema:** migration PRDs run alone, first, in the main checkout. Re-provision stream databases afterwards.
- **Registers and spec files:** `REQUIREMENT-COVERAGE.csv`, `OPEN-QUESTIONS.md`, reports 05–07 and the handoff note are
  written by the orchestrator only.
- **Merges, Demo, E2E:** in the main checkout, after the wave.

## After the wave (the orchestrator re-verifies; reports are claims)

```bash
git -C "$R" rev-parse --abbrev-ref HEAD            # must still be $FB
git -C "$R" rev-parse HEAD                         # must equal $BASE (nothing written to the main checkout)
git -C "$R" status --porcelain                     # must be empty
git -C "$WT" status --porcelain                    # per stream: empty
git -C "$WT" diff --name-only "$FB"...HEAD         # per stream: ⊆ owned files, ∩ forbidden = ∅
git -C "$WT" log --oneline "$FB"..HEAD             # commits named PRD-NN: …
```
1. Re-run each stream's gates yourself, or at least its PRD tests. Quote the counts verbatim.
2. Merge in dependency order:
   ```bash
   git -C "$R" merge --no-ff "$FB--$ID" -m "Merge stream $ID (PRD-NN)"
   ```
   `git merge` is on the `ask` list in `.claude/settings.json`, so the user approves each merge. On a conflict, stop;
   never resolve domain logic by guesswork. Re-plan ownership instead.
3. After the wave's merges, run the **full gates** in the main checkout (full suite vs baseline, plus PRD gates).
   Update the register and add a line to the handoff note.
4. Clean up only after a successful merge and with the user's agreement: `git worktree remove` (on the `ask` list),
   tear down the stream's local resources, delete stream branches with `git branch -d`.
5. Plan the next wave from the dependency graph.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Main checkout dirty or HEAD moved | A stream wrote to `<R>` using absolute main-checkout paths | Revert only that stream's paths; re-brief with `WT` paths |
| Stream is missing the spec or `.claude/` | Worktree created before the commit, or from the wrong base | Commit, recreate the worktree from `$FB` |
| `outOfScope` non-empty | Ownership too narrow, or scope creep | Don't merge. Re-assign ownership (spec change, orchestrator) or revert the extra paths |
| Login loops in the browser | Two streams share `localhost`, or a Secure cookie over HTTP | Use `<id>.localhost`; run the app in its local/dev mode |
| Stream DB creation fails | Template DB in use, or name collision | Close connections to the template; check the stream ID |
| Gates pass in the stream, fail after merge | Hidden coupling (shared fixture, registry, hotspot) | Treat as a QA bug: failing test, then fix on `$FB`, serially |
