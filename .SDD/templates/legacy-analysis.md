# <Feature> — Legacy analysis (source of truth when replacing an existing system)

Use this when the feature replaces or ports behaviour from an existing system. The existing system is **read-only**:
read its code, exports or screenshots; never write to its code or databases.

| Field | Value |
|---|---|
| Legacy system | <name, repo or location> |
| Source identifiers | class / route / screen / export ID |
| Source revision | repo SHA + date (or export provenance: who produced it, when) |
| Source hashes | sha256 of each source file that carries the rules (models, views, scripts, validators, exports) |
| Source hierarchy | which source wins per area: screenshots / legacy source or export / recorded decisions (OQ id or ADR, who decided, date) |
| as_of | date used for date- or age-relative test vectors |
| Oracles used | how behaviour was established: A running legacy app / B source reading / C export / D documentation only (D-only → "not independently verified") |
| Existing port | none / partial / full (repo + SHA) — a partial port turns this into a gap and parity audit |
| Variants covered | all variants inventoried in §11; first-release variant (or OQ id) |

## 1. Source file map
| Concern | File (path:line) | sha256 |
|---|---|---|

### 1a. Shared components
<Every shared partial, module or library the legacy behaviour depends on (recursively), with the §2 rows it contributes.>

## 2. Input and data inventory (per screen, endpoint or step)
### <Screen / endpoint N>
| Field | Label (verbatim) | Type | Options (value → label) | Required rule | Show/hide | Validation (message verbatim) | Default | Help text | Provenance |
|---|---|---|---|---|---|---|---|---|---|

Exit check: count of fields found in the source vs listed here or dropped in §12.

## 3. Navigation and gating
<Order, branching, early exits, disabled steps, which validation runs when.>

## 4. Client-side logic (pseudocode)
<Rules implemented in the browser; dead rules (selector ≠ posted value) marked.>

## 5. Business rules and calculations (pseudocode)
```
```
<Precedence exactly as legacy (order and variant effects), variant flags. "No derived decision" if none.>

## 6. Decision table
| Inputs … | Outcome | Persisted values |
|---|---|---|

## 7. Outcome and message texts (verbatim)
<Every alert, modal body and status text; note where client-side text overrides server-side text.>

## 8. Back-end lifecycle (pseudocode with path:line hops)

**Trust boundary** (mandatory): <which values the server recomputes vs accepts from the request (hidden fields,
posted decisions), with path:line.>

### 8a. Blocked, abandoned and draft states
<What legacy persists for blocked or abandoned flows and for drafts (records, statuses, reporting).>

### 8b. Alternative actions, notifications and integrations
<Extra actions, notifications, callbacks, outbound integrations; anything outside this system's boundary is recorded
as an open question.>

## 9. Data model (tables/columns written)

## 10. Generated documents and exports
<Section order; what is printed or omitted.>

## 11. Variants (differences as data)
<Every variant (subclasses, configuration flags, regional forks) and how it differs.>

## 12. Legacy quirks
| Quirk | Evidence | Replicate? (Yes / No → ADR) |
|---|---|---|

## 13. Test vectors
<Link to `test-vectors/` files. Written by a separate subagent; each vector cites `path:line`, oracle and an
`ambiguous` flag. Include boundary values at `as_of` and every blocking outcome.>

## 14. Open questions
| ID | Question | Owner (product / domain owner / dev) | Blocks |
|---|---|---|---|
<Seeds `OPEN-QUESTIONS.md` at the spec-folder root. For every domain question about an existing legacy rule
(threshold, missing-value branch, wording), also record the legacy-verbatim value with `path:line`: the new system
keeps that value, tagged `ruling_pending: "<id>"`, until the owner rules — never "undefined pending the ruling".>
