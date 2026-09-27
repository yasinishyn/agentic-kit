# <Feature> — Architecture

## 1. Context (DDD)
- Bounded context(s) touched and their responsibility.
- Domain model: entities, value objects, domain services (e.g. pure decision function).
- Anti-corruption layer to legacy concepts (if porting).

## 2. Component view
```mermaid
flowchart LR
```

## 3. Reuse map
| Need | Existing component (path) | Change required |
|---|---|---|

## 4. Decisions
| ADR | Title | Status |
|---|---|---|

## 5. PRDs (implementation slices)
| PRD | Scope | Owned files | Depends on |
|---|---|---|---|

## 6. Test strategy
L1 unit · L2 integration · L3 localhost system/parity/browser · L4 shared-environment check by a human (levels to be confirmed with the project owner).

## 7. Safety and business-critical rules
Risk candidates (RISK-<AREA>-NN), controls, links to tests. Domain-owner review required before release where the project needs it.

## 8. Security
STRIDE summary + localhost abuse cases (tampered derived fields, step skipping, CSRF, IDOR, XSS/template injection in rendered text or documents, SSRF in renderers or fetchers, polyglot uploads).

## 9. Acceptance criteria and definition of done
