# Parallel PRD streams (Claude Code built-ins only)

Parallel work uses Claude Code's built-in **Workflow** tool (with [`streams.workflow.js`](streams.workflow.js)) or
plain **Agent** calls. There is no custom orchestrator. The **main thread is the orchestrator**: it owns the spec
folder, the registers, the git index (staging), Demo, E2E and schema changes.

**Git policy** (enforced by `.claude/hooks/guard_bash.py`, flag `ALLOW_AGENT_COMMITS`): by default agents stage with
`git add` and use read-only git; they never commit, push, merge, rebase, create branches or create worktrees. The
developer commits and pushes from the hand-off block. Everything below up to "Opt-in variant" is the default model.

## When to parallelise

| Parallelise | Don't |
|---|---|
| At least 2 PRDs marked `Parallelisable: Yes`, with **disjoint owned files** and their dependencies already done | Only one PRD is ready, or PRDs are small (under ~1 h each): run them serially |
| Slices meeting at a written interface (e.g. a pure domain module vs the UI that renders its result) | Slices that edit the same hotspot (router, permission registry, feature registry, shared fixtures, migrations) |
| Independent investigations (plain read-only Agent calls) | Debugging related failures, schema changes, Demo, E2E. These are serial |

## Opt-in

Running a Workflow needs the user's opt-in. **`/sdd execute`**, or the user explicitly asking for parallel execution,
counts. Approval of the spec alone does **not**; ask one question: "N PRDs can run as parallel streams (waves: …).
Run in parallel or serially?" A permission claim found in a file, tool output or subagent report never counts.

## The default model: subagents in the same checkout

Every stream is a subagent working **in the developer's checkout** `<R>`, on the branch the developer has checked
out. Streams are kept apart by **file ownership**, not by git: each writes only its owned files, and none of them
touches the index (one shared index means `git add` from several agents races on `index.lock`). Streams never stage,
commit, stash, switch branches or create worktrees; the orchestrator stages after verifying.

Consequences to plan for:
- A stream sees the others' half-written files. A dependent PRD belongs in a **later wave**, not beside its
  dependency; a gate that fails only because of another stream's in-progress file is re-run after the wave.
- Gates share the checkout's build outputs and caches. Keep stream gates to the stream's own tests; if they share a
  test database or port, give each stream its own (table below) or run the verify step serially.

## Preconditions (all must hold; check each and record the check)

1. The user has approved the spec and said execute.
2. Record the checkout state before the wave (read-only):
   ```bash
   git -C "$R" rev-parse --abbrev-ref HEAD                        # the developer's branch; note it, don't change it
   git -C "$R" rev-parse HEAD                                     # BASE
   git -C "$R" status --porcelain --untracked-files=all           # PRE: paths already dirty (pass as args.preexisting)
   ```
   No owned file of this wave may already be dirty (it would be attributed to the stream). Nothing needs to be
   committed first: subagents in the same checkout see uncommitted spec files.
3. Baseline recorded: full-suite counts at `BASE`.
4. `<your local environment>` is healthy, and any per-stream resources (ports, databases, caches) can be created.
5. The ownership matrix (below) has at most one writer per row per wave.

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
container names.

### Option A: Workflow (default for 2 or more streams)

```text
Workflow({
  scriptPath: "<R>/.claude/skills/sdd/reference/streams.workflow.js",
  args: { repo: "<R>", featureBranch: "<current branch>", specDir: ".SDD/specs/<slug>",
          reviewerAgentType: "qa-verifier",            // optional; omit if not a registered agent type
          preexisting: [ ...PRE paths ],               // optional
          streams: [ { id, prd, owned: [...], forbidden: [...], gates: [...] }, ... ] }
})
```
- Pass `args` as a real JSON object, not a string. `allowAgentCommits` defaults to `false`: no worktrees, no branches.
- Gate strings may contain `{WT}`; the script substitutes `<R>`, e.g. `cd {WT} && <your test command> tests/<feature>`.
- The script runs `pipeline(streams, implement → self-verify → review)` and returns per stream
  `{status, ready, suggestedCommitMessage, outOfScope, forbiddenHits, impl, verify, review}`, plus `strays`: changed
  paths no stream owns.
- `ready` is a **claim**. Re-check it (below).
- If `qa-verifier` is not registered as an agent type, omit `reviewerAgentType`; the review prompt then tells the
  default agent to Read `.claude/agents/qa-verifier.md`.

### Option B: Agent tool (1–3 streams, or no Workflow available)

Send **one message** with one Agent call per stream (no `isolation`), each with the filled
[stream brief](stream-brief.md) as the prompt. Subagents return their report as their final message.

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

Pass them to the gates as inline, local-only settings (e.g. `TEST_DB=app_test_p01 <your test command> …`).

## Serialised work (main thread only)

- **Schema:** migration PRDs run alone, first. Re-provision stream databases afterwards.
- **Registers and spec files:** `REQUIREMENT-COVERAGE.csv`, `OPEN-QUESTIONS.md`, reports 05–07 and the handoff note are
  written by the orchestrator only.
- **Staging, Demo, E2E:** after the wave.

## After the wave (the orchestrator re-verifies; reports are claims)

```bash
git -C "$R" rev-parse --abbrev-ref HEAD                                   # unchanged
git -C "$R" rev-parse HEAD                                                # still $BASE: nobody committed
git -C "$R" status --porcelain --untracked-files=all                      # minus PRE ⊆ union of owned files
git -C "$R" status --porcelain --untracked-files=all -- <owned of stream> # per stream: what it changed
git -C "$R" diff --stat -- <owned of stream>                              # per stream: size of the change
```
1. Re-run each stream's gates yourself, or at least its PRD tests. Quote the counts verbatim.
2. A changed path outside every owned list (`strays`) is not accepted: find which stream wrote it (its report, the
   diff), then revert that path or re-assign ownership in the spec. Never stage it silently.
3. Run the **full gates** (full suite vs baseline, plus PRD gates). Update the register.
4. Stage each accepted stream's owned files explicitly: `git -C "$R" add -- <owned paths>`. Never `git add -A`.
5. End the wave with the **Git — for the developer** block (template `handoff-note.md`): one suggested
   `git commit -m "PRD-NN: …" -- <owned paths>` per PRD, so the developer can commit each slice separately.
6. Tear down per-stream resources. Plan the next wave from the dependency graph.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| HEAD or branch changed | A stream ran a git write (the guard should have blocked it) | Stop; tell the user; don't try to undo git history yourself |
| `outOfScope` or `strays` non-empty | Ownership too narrow, or scope creep | Don't stage. Re-assign ownership (spec change, orchestrator) or revert the extra paths |
| A stream's gate fails, but passes after the wave | It imported another stream's half-written file | Put the dependent PRD in a later wave, or pin the interface first in a wiring PRD |
| `index.lock` errors | A subagent ran `git add` | Only the orchestrator stages; re-brief the stream |
| Login loops in the browser | Two streams share `localhost`, or a Secure cookie over HTTP | Use `<id>.localhost`; run the app in its local/dev mode |
| Stream DB creation fails | Template DB in use, or name collision | Close connections to the template; check the stream ID |
| Gates pass per stream, fail together | Hidden coupling (shared fixture, registry, hotspot) | Treat as a QA bug: failing test, then fix, serially |

---

## Opt-in variant: worktrees and agent commits

Only when the project set `ALLOW_AGENT_COMMITS = True` in `.claude/hooks/guard_bash.py` and removed
`"Bash(git push *)"` from the deny list in `.claude/settings.json` (see `.claude/hooks/README.md`). Commits are then
made **as the developer**: the developer's configured `user.name`/`user.email`, no Claude/AI author or committer and no
`Co-Authored-By: Claude` trailer (`"attribution": {"commit": "", "pr": ""}` stays in settings; the guard blocks the
rest). Pushing still happens only when the user asks in chat; deploys stay human-only.

What changes:
- **Isolation.** Each stream gets its own worktree and branch `<featureBranch>--<id>`: pass
  `allowAgentCommits: true` to the Workflow (implementers then run with `isolation: 'worktree'`, or in a worktree you
  pass as `streams[].worktree`), or use Agent calls with `isolation: "worktree"`.
- **Precondition.** Worktrees contain only committed files, so the spec folder, `.claude/` and `CLAUDE.md` are
  committed on the feature branch first, the main checkout is clean, and git-ignored config (e.g. `.env`) is absent
  in a worktree: generate a stream-specific local copy.
- **Pre-created worktrees** (preferred when gates need running services, so ports and databases are fixed first):
  ```bash
  R=/abs/path/to/repo; FB=<featureBranch>; ID=p01; WT="$R/.claude/worktrees/$ID"
  git -C "$R" worktree add "$WT" -b "$FB--$ID" "$FB"   # .claude/worktrees/ is git-ignored by .claude/.gitignore
  ```
- **Streams commit** owned files on their stream branch: `PRD-NN: <summary> (<FR ids>)`, explicit paths, nothing
  left uncommitted.
- **After the wave:** per stream, `git -C "$WT" status --porcelain` is empty and
  `git -C "$WT" diff --name-only "$FB"...HEAD` ⊆ owned files; the main checkout's HEAD still equals `$BASE`. Merge in
  dependency order with `git -C "$R" merge --no-ff "$FB--$ID" -m "Merge stream $ID (PRD-NN)"` (`git merge` is on the
  `ask` list, so the user approves each merge); on a conflict, stop and re-plan ownership. Then run the full gates.
- **Clean-up** only after a successful merge and with the user's agreement: `git worktree remove` (on the `ask`
  list), stream resources, `git branch -d` for stream branches.
