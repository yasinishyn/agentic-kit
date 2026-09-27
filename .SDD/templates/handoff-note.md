# <Feature> — Hand-off note (for the human who pushes)

| Branch | Commits | Base |
|---|---|---|

## What changed

## Evidence
- Tests: … (verbatim counts)
- QA report / Demo / E2E: links

## Migrations
<Written but NOT applied to any shared DB. Apply order and target environments.>

## Residual risks and open questions
<Risks, and OQ ids with owner and status. Messages to people are drafted in chat, not here.>

## Commands for the user (agents never run these; the guard hook blocks them)
```bash
git -C <repo> log --oneline <base>..HEAD
git -C <repo> push origin <branch>
```
