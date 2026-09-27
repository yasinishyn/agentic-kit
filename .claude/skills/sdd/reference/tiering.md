# SDD tiering: decision table and examples

Pick the tier **before** doing anything else, and write it in the discovery header with the reason. When unsure, take
the heavier tier. If a Full trigger turns up mid-task, the tier upgrades straight away: stop, tell the user, and start
the missing stages. A tier never downgrades.

## Decision table (first matching row wins)

| # | Question | Yes → |
|---|---|---|
| 1 | Does it change a business-critical outcome: money, eligibility, pricing, thresholds, legal or regulated wording, routing of data to other parties, anything a domain owner must sign off? | **Full** |
| 2 | Does it change a core engine or framework layer that many features depend on? | **Full** |
| 3 | Does it add or alter a migration, table or column? | **Full** |
| 4 | Does it touch authentication, sessions, permissions or access policy? | **Full** |
| 5 | Does it add an endpoint that reads or writes personal, customer or other sensitive data? | **Full** (IDOR, permission and inventory surface) |
| 6 | Does it change an external integration or its client (payment, e-mail, storage, third-party APIs, queues)? | **Full** |
| 7 | Is it more than 2 files, or any UI, within one module, and none of the above? | **Feature-lite** |
| 8 | Is it at most 2 files, with none of the above? | **Trivial** |

Adapt rows 1–6 to your project in `CLAUDE.md` (name the critical modules and hotspot files) rather than editing this
table per feature.

## What each tier produces

| Tier | Spec folder | Stages | Minimum evidence |
|---|---|---|---|
| Trivial | none | Developer → QA | Relevant tests + full suite (verbatim counts) in the reply; changed files staged and a suggested commit message; `/code-review` on the diff |
| Feature-lite | `01-discovery.md` (1 page), `handoff-note.md` | Discovery-lite → Developer → QA → Demo if UI | Short `05-qa-report.md`; `06-demo.md` if UI; abuse-checklist rows for any new route |
| Full | Everything in `.SDD/README.md` (layout) | All six + handoff | Every report; register complete; ADRs signed by the named deciders |

## Examples

| Request | Tier | Why |
|---|---|---|
| Fix a typo in an admin page heading | Trivial | 1 template, no critical wording |
| Make a date-dependent unit test deterministic | Trivial | Test-only |
| Add a non-sensitive column and filter to one admin list | Feature-lite | One module, UI, so Demo is needed |
| Refactor a helper used in 4 modules, no behaviour change | Feature-lite | More than 2 files; the full suite proves no change |
| Bump a library version | Feature-lite; **Full** if it affects auth, crypto, serialisation or document rendering | Wide blast radius |
| Change legally required or contractual wording shown to users | **Full** (Discovery and Architect may be a page each) | Needs sign-off from its owner |
| Add a new role or permission, or change default grants | **Full** | Permissions |
| New endpoint for an admin page showing only non-sensitive config | Feature-lite + abuse-checklist rows (CSRF, permissions, inventory) | Still an attack surface |
| New endpoint returning customer or user data | **Full** | IDOR/tenancy surface |
| Change pricing, scoring or eligibility rules | **Full** + domain owner | Domain ruling; STOP for decisions |
| Add seed data for local testing only | Trivial if it lives only in the local seed; **Full** if it is a migration | Local seed is not product code |
| Change a queue, webhook or outbound-message handoff | **Full** | Integration; lost or duplicated messages |

## Upgrade signals during work

Upgrade the tier when you discover any of these:
- a needed migration;
- a new endpoint;
- a new or changed permission;
- a derived business-critical value;
- an external call;
- more than one module;
- existing behaviour whose replication or change nobody has decided.

## Critical-content checklist (row 1 helper)

Treat it as critical if any answer is yes:
1. Could a different value change what a user is charged, granted, told or sent?
2. Does someone read it to make a decision with real consequences?
3. Does it gate who may use a feature (age, eligibility, subscription, tenancy)?
4. Would a domain owner, compliance or legal expect to review the change?
