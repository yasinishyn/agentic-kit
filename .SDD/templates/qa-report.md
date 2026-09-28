# <Feature> — QA report

| Run | Date | Branch · base commit · staged/uncommitted |
|---|---|---|

## 1. Test results (verbatim counts, vs the baseline)
## 2. Code review findings (/code-review)
| # | Severity | Finding | File:line | Status |
|---|---|---|---|---|
## 3. Security review (/security-review + localhost abuse checklist)
## 4. Independent verification (qa-verifier verdict)
## 5. Bugs
| # | Severity | Title | Requirement | Failing test (path::name) | Expected / actual | Status (open / fixed / closed) | Root cause · fix |
|---|---|---|---|---|---|---|---|

Severity: **Critical** blocks core functionality, no workaround · **High** major function broken, workaround exists ·
**Medium** partly works, minor impact · **Low** cosmetic. Fixed one at a time; a fix never edits the test that caught
the bug.
## 6. Residual risks
