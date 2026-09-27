---
name: verification-before-completion
description: Use when about to claim work is complete, fixed or passing - before committing, handing off a PRD or stream, filling a QA, demo, E2E or hand-off report, moving to the next SDD stage, or relaying a subagent's success. Requires running the proving command fresh (the test suite, git diff against owned files, a browser check for UI) and quoting its output; evidence before assertions, always.
---

<!-- kit patch: provenance note; description sharpened (upstream: "Use when about to claim work is complete, fixed, or passing, before committing or creating PRs - requires running verification commands and confirming output before making any success claims; evidence before assertions always") -->
> Vendored from obra/superpowers@8ca22db (v6.4.2) by Jesse Vincent, MIT — licence in `LICENSES/superpowers-LICENSE`.
> Local changes are marked `<!-- kit patch: … -->` … `<!-- /kit patch -->`.
<!-- /kit patch -->

# Verification Before Completion

## Overview

**Core principle:** Evidence before claims, always.

**Violating the letter of this rule is violating the spirit of this rule.**

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the verification command in this message, you cannot claim it passes.

## The Gate Function

```
BEFORE claiming any status or expressing satisfaction:

1. IDENTIFY: What command proves this claim?
2. RUN: Execute the FULL command (fresh, complete)
3. READ: Full output, check exit code, count failures
4. VERIFY: Does output confirm the claim?
   - If NO: State actual status with evidence
   - If YES: State claim WITH evidence
5. ONLY THEN: Make the claim

Skip any step = lying, not verifying
```

## Common Failures

| Claim | Requires | Not Sufficient |
|-------|----------|----------------|
| Tests pass | Test command output: 0 failures | Previous run, "should pass" |
| Linter clean | Linter output: 0 errors | Partial check, extrapolation |
| Build succeeds | Build command: exit 0 | Linter passing, logs look good |
| Bug fixed | Test original symptom: passes | Code changed, assumed fixed |
| Regression test works | Red-green cycle verified | Test passes once |
| Agent completed | VCS diff shows changes | Agent reports "success" |
| Requirements met | Line-by-line checklist | Tests passing |
<!-- kit patch: SDD-specific claims -->
| UI works | Built-in browser on localhost as a seeded test user: page text or screenshot of the expected state | Template renders in a unit test |
| Stream/PRD done | `git diff --name-only <base>..HEAD` ⊆ PRD owned files, plus PRD gates green | Stream's own summary |
| Requirement covered | Test id or evidence row in `registers/REQUIREMENT-COVERAGE.csv` | "Covered by the new tests" |
<!-- /kit patch -->

## Red Flags - STOP

- Using "should", "probably", "seems to"
- Expressing satisfaction before verification ("Great!", "Perfect!", "Done!", etc.)
<!-- kit patch: agents never push or open PRs -->
- About to commit, hand off, or tell the user the branch is ready to push without verification
<!-- /kit patch -->
- Trusting agent success reports
- Relying on partial verification
- Thinking "just this once"
- Tired and wanting work over
- **ANY wording implying success without having run verification**

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should work now" | RUN the verification |
| "I'm confident" | Confidence ≠ evidence |
| "Just this once" | No exceptions |
| "Linter passed" | Linter ≠ compiler |
| "Agent said success" | Verify independently |
| "I'm tired" | Exhaustion ≠ excuse |
| "Partial check is enough" | Partial proves nothing |
| "Different words so rule doesn't apply" | Spirit over letter |

## Key Patterns

**Tests:**
```
✅ [Run test command] [See: 34/34 pass] "All tests pass"
❌ "Should pass now" / "Looks correct"
```

**Regression tests (TDD Red-Green):**
```
✅ Write → Run (pass) → Revert fix → Run (MUST FAIL) → Restore → Run (pass)
❌ "I've written a regression test" (without red-green verification)
```

**Build:**
```
✅ [Run build] [See: exit 0] "Build passes"
❌ "Linter passed" (linter doesn't check compilation)
```

**Requirements:**
```
✅ Re-read plan → Create checklist → Verify each → Report gaps or completion
❌ "Tests pass, phase complete"
```

**Agent delegation:**
```
✅ Agent reports success → Check VCS diff → Verify changes → Report actual state
❌ Trust agent report
```

<!-- kit patch: the SDD gates and where the evidence goes -->
## SDD Gates

| Claim | Command / evidence | Where it is recorded |
|---|---|---|
| Targeted tests pass | `<your test command> <path>` | PRD report (verbatim count line) |
| Suite green | `<your test command>` (full suite) | PRD report, `05-qa-report.md` |
| Scope respected | `git diff --name-only <base>..HEAD` vs the PRD's "Owned files" | PRD report |
| Reviewed | `/code-review` and `/security-review` output, findings triaged | `05-qa-report.md` |
| Independent check | `qa-verifier` agent's final `VERDICT:` block | `05-qa-report.md` |
| UI / journey | Built-in browser as a seeded test user on localhost | `06-demo.md` |
| Project automation | `<your e2e command>` and its verbatim result; external writes dry-run only | `07-e2e-report.md` |

Run every command against `<your local environment>`, never shared or production services. Quote counts exactly as
printed ("412 passed, 3 skipped"), never rounded or summarised. A failure you did not cause is still reported by name.
Evidence never contains personal data or secrets.
<!-- /kit patch -->

## When To Apply

**ALWAYS before:**
- ANY variation of success/completion claims
- ANY expression of satisfaction
- ANY positive statement about work state
<!-- kit patch: hand-off instead of PR creation -->
- Committing, writing the hand-off note, task completion
<!-- /kit patch -->
- Moving to next task
- Delegating to agents

**Rule applies to:**
- Exact phrases
- Paraphrases and synonyms
- Implications of success
- ANY communication suggesting completion/correctness
