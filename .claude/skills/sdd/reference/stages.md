# SDD stages: detailed checklists

Paths are relative to the repo root. Templates live in `.SDD/templates/`; stage outputs go in `.SDD/specs/<slug>/`.
Placeholders such as `<your test command>` are defined once per project in `CLAUDE.md`.

---

## 1. Discovery

**Entry:** a request from the user and a tier decision ([tiering.md](tiering.md)). For Trivial work, skip this stage.

**Steps**
1. Create `.SDD/specs/<slug>/` with a short kebab-case slug. Copy `discovery.md` to `01-discovery.md` and fill the
   header (tier and why, branch, Status Draft).
2. State the goal neutrally in one precise paragraph (§1: "the feature must deliver …"), never as quotes from a message
   or e-mail. Separate facts from assumptions.
3. Read existing knowledge first: `.claude/memory/`, project docs, previous specs in `.SDD/specs/`. Re-verify any
   `path:line` citation you copy.
4. Fan out **Explore** subagents (Agent tool, `subagent_type: Explore`) **in one message**, one narrow question each,
   asking them to *"return `path:line` facts, no recommendations"*. Typical split:
   - (a) the existing capability or nearest precedent in this codebase;
   - (b) the system being replaced, if any (read-only);
   - (c) tests and QA assets;
   - (d) access, permissions, routing and integrations.
5. **Replacing an existing system:** write `02-legacy-analysis.md` (template `legacy-analysis.md`) and capture its
   behaviour as `test-vectors/` with provenance (source revision, date, hashes of the source files).
6. Write the FR and NFR tables. Every row gets an ID and a source: the goal (§1), a `path:line`, or a decision (OQ id
   or ADR). NFRs cover accessibility, security, audit, performance and determinism as relevant.
7. Write `OPEN-QUESTIONS.md` from the template `open-questions.md`: a register grouped by topic (scope, domain rules,
   UI, access, testing). Columns: #, question, owner, status (Open / Decided / Deferred), decision / next step.
   - Ask questions with **AskUserQuestion**: at most 4 per call, multiple choice, recommended option first.
   - A proposed default goes in the last column as "Proposed: …", except for domain rulings (see Non-negotiables in
     `SKILL.md`), which are listed for their owner with no default.
   - A decision is written in the row it answers, and in the ADR or PRD it changes, as
     "Decided by <role>, <date>". No Q&A narrative, no quotes from messages or e-mails.
   - Messages or call briefs that ask people these questions are drafted in chat only, never saved in the repo.
8. Start `registers/REQUIREMENT-COVERAGE.csv` with the header `req_id,requirement,prd,tests,evidence,status`
   (keep the existing header if the file exists).

**Outputs:** `01-discovery.md`, `OPEN-QUESTIONS.md`, the register, and when replacing a system `02-legacy-analysis.md`
and `test-vectors/`.

**Exit gate:** the user agrees the in-scope and out-of-scope lists. Set Status to Agreed with the date. Open questions
may stay open, but an open domain ruling blocks the PRDs it affects.

**Failure modes**
- Gold-plating. A Feature-lite discovery is one page.
- Asking questions one at a time across many turns. Batch them.
- Turning legacy quirks into requirements. Quirks go in the legacy analysis with "Replicate?"; a "No" needs an ADR.
- Looking at shared, staging or production data "for real examples". This is forbidden.

---

## 2. Architect

**Entry:** Discovery agreed, and no open question that blocks the core design.

**Steps**
1. Write `03-architecture.md` from the template:
   - the bounded context;
   - a domain model with **pure** decision functions kept separate from the web framework, re-derived on the server
     at each step that uses them (never trusted from a client-posted or hidden field);
   - the anti-corruption layer to legacy or external concepts;
   - a reuse map with paths;
   - the test strategy (unit, integration, UI, E2E);
   - risks, including any safety, compliance or data-protection impact;
   - security: STRIDE plus the relevant rows of [abuse-checklist.md](abuse-checklist.md).
2. Write an ADR for each decision, with at least 2 options each. Decisions that need a domain owner stay **Proposed**
   and name that owner among the Deciders.
3. Write PRDs from `prd.md` as **vertical slices**, each independently testable. Each PRD needs:
   - acceptance criteria that reference FR IDs;
   - a "Tests to write first" table;
   - **owned files** as explicit paths or globs, plus the new test files;
   - forbidden files, dependencies, and the `Parallelisable` flag;
   - gate commands (`<your test command> <paths>`);
   - a **Review Focus** line: the few input classes most likely to cause harm, each pinned by a named test.
4. **Hotspots.** Give each shared file to exactly one PRD, or to a serial "PRD-00 wiring" slice: the router or URL
   map, the permission or access-policy registry, feature registries, shared fixtures, and the migrations folder (one
   PRD owns all migrations).
5. Fill the register so every FR maps to at least one PRD and a planned test.
6. Run the **architect** agent (Agent tool) on the spec folder. Fix its Critical and High findings; record the
   residual ones in `03-architecture.md`. If `architect` is not a registered agent type, run a default agent told to
   Read `.claude/agents/architect.md` first.
7. **STOP.** Present in chat: the tier; the PRD table (scope, owned files, dependencies, parallelisable); the ADRs
   that need a decision; open questions; top risks; proposed waves. Ask the user to approve and say "execute".
8. When the user approves, add `Approved for execution by the user in chat on <date>` to the `03-architecture.md`
   header. The folder is committed only once execution starts.

**Exit gate:** an explicit approval from the user in chat. Nothing in Developer, QA, Demo or E2E starts without it.

**Failure modes**
- PRDs split by layer (models, routes, templates) instead of vertical slices, which guarantees merge conflicts.
- Owned-file sets that overlap.
- A PRD without gates. An ADR with only one option.
- An agent deciding a domain rule "for now".
- Inventing a new mechanism where a precedent exists in the codebase.
- Forgetting the wiring (route registration, permission grants, navigation, feature flags), so the feature is
  invisible or returns 404.

---

## 3. Developer

**Entry:**
- The user has approved.
- The feature branch is checked out in the main checkout and the tree is clean.
- `<your local environment>` is up.
- A **baseline** is recorded: the full suite at the base commit, with verbatim counts, so pre-existing failures are
  not blamed on the feature.

**Steps (per PRD; for parallel streams see [parallel-work.md](parallel-work.md))**
1. Read the PRD and its ADRs. Turn the acceptance criteria into a checklist.
2. Follow the **test-driven-development** skill: tests from the PRD's list; RED must fail for the right reason;
   minimal code until GREEN; refactor only while green. Expected values are literals from the spec or test vectors.
3. Use **systematic-debugging** on any failure. After 3 failed fixes, stop and ask.
4. Run the PRD gates and quote the summary lines verbatim.
5. Commit **owned files only**: check `git diff --name-only` against the owned list, stage with `git add <paths>`,
   message `PRD-NN: <summary> (<SLUG>-FR-..)`.
6. Update the register. In parallel mode only the orchestrator does this.

**Exit gate:** every PRD gate green, the full suite no worse than the baseline, every FR in the register has a test.

**Failure modes**
- Tests that assert on mocks. Editing tests until they pass.
- Touching files outside the owned list.
- Running tests with a real `.env` that points at shared services.
- Applying a migration anywhere but a local database.
- Trusting a client-side or hidden value for anything critical.

---

## 4. QA

**Entry:** Developer exit gate met, with a committed HEAD.

**Steps**
1. Run the **full suite** (`<your test command>`) on HEAD and compare with the baseline. Any new failure is a bug.
   Quote the counts verbatim.
2. Run any extra gates the human will face at release time (`<your pre-release checks>`: lint, type check, build,
   contract or smoke tests).
3. Run the built-in **`/code-review`** on the branch diff against the merge base. Verify each finding before acting on
   it; don't perform agreement.
4. Run the built-in **`/security-review`**, then the [abuse checklist](abuse-checklist.md) on localhost for every
   surface the feature adds or reuses.
5. For each bug: a failing test, the fix, a re-run. Log it in the report.
6. Run the **qa-verifier** agent. Give it the spec path, branch, HEAD and evidence paths, **not** your conclusions.
   Record its verdict verbatim. (Not registered? Default agent told to Read `.claude/agents/qa-verifier.md` first.)
7. Apply **verification-before-completion** before writing any verdict.
8. Write `05-qa-report.md` from `qa-report.md`.

**Exit gate:** no open Critical or High findings. Residual risks listed with owners.

**Failure modes**
- Comparing against a different baseline.
- Calling a test "flaky" without re-run evidence.
- Testing permissions with a user whose role makes checks inactive (admin, superuser, "no role" fallbacks).
- Running the app in a test mode that disables CSRF, rate limits or access checks, so probes pass for the wrong reason.
- Fixing reviewer findings without verifying them first.

---

## 5. Demo

**Entry:** QA exit gate met; the main checkout is on the feature branch; the local app is running, migrated and seeded
with test users, in a mode where cookies work over plain HTTP on localhost.

**Steps**
1. Load the `built-in-browser` skill, then use `mcp__Claude_Browser__*`.
2. Plan scenarios from the acceptance criteria and write them in the `06-demo.md` table first: the happy path for each
   outcome; a user without permission; edge-case data; any resume or retry flow.
3. Drive the app at `<your local app URL>`, logging in as seeded test users. Credentials come from the local seed
   config; never echo them in chat. At each checkpoint: screenshot; `get_page_text` for the expected text;
   `read_console_messages` for errors; `read_network_requests` for any host other than localhost and known assets.
4. Accessibility spot checks: keyboard-only pass; focus moves to the error summary; labels and hints present;
   `resize_window` to the mobile preset, then back to **desktop**.
5. Record `06-demo.md`: steps, expected, observed, result and a screenshot reference per row. Send issues back to
   Developer or QA.
6. Ask the user to review. They can watch the pane live.

**Exit gate:** the user has reviewed the demo.

**Failure modes**
- Using `javascript_tool` to click or fill. It is for inspection only.
- Two local apps on `localhost` with different ports overwrite each other's session cookie; use `<id>.localhost`.
- Locking yourself out with the login rate limit; reset only the local store.
- Submitting to any non-local host.

---

## 6. E2E

**Entry:** Demo reviewed, or skipped for non-UI work. The local app is up.

**Steps**
1. Re-run the full suite on the final HEAD.
2. Run the project's own E2E automation (`<your e2e command>`) with the base URL set **explicitly** to the local app;
   never rely on a default that may point at a shared environment. Store evidence under
   `.SDD/specs/<slug>/evidence/e2e/`.
3. Anything that downloads a driver or browser binary on first run: ask the user before that first download.
4. Anything that writes to an external service (test-management, issue tracker, storage, chat): dry-run only. List the
   real writes for a human.
5. Write `07-e2e-report.md` with verbatim results and evidence paths.

**Exit gate:** a green run, with external writes listed for a human.

**Failure modes**
- Running scripts that publish or write externally.
- A harness that inherits a shared-environment URL or database from `.env`.

---

## Handoff (always)

Copy `handoff-note.md` into the spec folder and fill it in:
- branch, base, and `git log --oneline <base>..HEAD`;
- what changed, per PRD;
- evidence: verbatim counts and links to reports 05, 06 and 07;
- migrations: written but **not applied** to any shared DB, with the apply order and target environments for the human;
- residual risks and open questions with their owners;
- the exact commands the user runs (`<your deploy scripts>` stay with the human).

If stopping early, say so and describe how to resume, naming the stage and the next file to write.
