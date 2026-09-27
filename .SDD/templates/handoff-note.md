# <Feature> — Hand-off note (for the developer who commits and pushes)

| Branch | Base commit | Staged (`git diff --cached --stat`, last line) |
|---|---|---|

## What changed
<Per PRD: summary and owned files.>

## Evidence
- Tests: … (verbatim counts)
- QA report / Demo / E2E: links

## Migrations
<Written but NOT applied to any shared DB. Apply order and target environments.>

## Residual risks and open questions
<Risks, and OQ ids with owner and status. Messages to people are drafted in chat, not here.>

## Git — for the developer
Agents staged the work and did not commit or push (guard default `ALLOW_AGENT_COMMITS = False`). Review, then run:
```bash
git -C <repo> switch -c <feature-branch>          # only if the work is still on the default branch
git -C <repo> status --short && git -C <repo> diff --cached --stat
git -C <repo> add -- <any path listed as unstaged above that belongs to the feature>
git -C <repo> commit -m "<slug>: spec (discovery, architecture, ADRs, PRDs)" -- .SDD/specs/<slug>
git -C <repo> commit -m "PRD-01: <summary> (<SLUG>-FR-01, …)" -- <PRD-01 owned paths>
git -C <repo> commit -m "PRD-02: <summary> (<SLUG>-FR-..)" -- <PRD-02 owned paths>
git -C <repo> push -u origin <feature-branch>
```
<!-- If the project opted in to agent commits, replace the block above with the commits already made
     (`git -C <repo> log --oneline <base>..HEAD`) and the push command. -->

## Deploy (human only)
<Steps and `<your deploy scripts>` the developer runs, if any.>
