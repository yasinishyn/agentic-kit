# <Feature> — Architecture

| Field | Value |
|---|---|
| Spec | `<slug>` · [01-discovery.md](01-discovery.md) |
| Tier | Feature-lite / Full |
| Status | Draft |

## 0. Codebase context
| Area | Existing component (path) | Pattern to match / constraint / likely conflict |
|---|---|---|

## 1. Context (DDD)
- Bounded context and its responsibility.
- Domain model: entities, value objects, **pure** decision functions (re-derived on the server, never trusted from a client).
- Anti-corruption layer to legacy or external concepts.

## 2. Components
| Component | Path | Owner PRD |
|---|---|---|

## 3. Reuse map
| Need | Existing component (path) | Change required |
|---|---|---|

## 4. Decisions
| ADR | Title | Status |
|---|---|---|

## 5. Execution map (PRDs and waves)
| PRD | Slice | Wave | Requires | Owns (files and shared artefacts) | Parallelisable |
|---|---|---|---|---|---|

Within a wave the `Owns` sets are disjoint. **Map review:** <no blocking conflicts | REVISED (n) — conflict-free>.
New technology only with an ADR saying why the existing stack cannot do it.

## 6. Test strategy
| Layer | Tool | Files (owner PRD) |
|---|---|---|

## 7. Security
STRIDE summary and the relevant localhost abuse cases (tampered fields, step skipping, CSRF, IDOR, injection).

## 8. Risks (residual)
| Risk | Mitigation / owner |
|---|---|
