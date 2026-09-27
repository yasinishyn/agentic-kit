# Abuse checklist: localhost security pass for web features

Used in **QA step 4** (after the built-in `/security-review`) and in the **Architect** security section (copy the
relevant rows into `03-architecture.md`). Results go in `05-qa-report.md`. Re-open code before citing `path:line`;
line numbers drift.

## Rules of engagement

- **Targets:** only 127.0.0.1 and `*.localhost` (the local app at `<your local app URL>`, or the framework's in-process
  test client). Never staging, production or any external host. Don't download scanners or fuzzers; ask first if one
  seems necessary.
- **Environment:** run the app in its local/dev mode with security controls **on**. Many frameworks' "testing" mode
  disables CSRF, rate limits or access checks; probes run there pass for the wrong reason. If tests need that mode,
  re-enable each control explicitly in the abuse tests.
- **Data:** seeded test users and synthetic records only. Payloads are inert canaries: `alert(1)`, `{{7*7}}`, a local
  HTTP canary. No real malware; the EICAR test string only with a local scanner switched on.
- **Credentials:** test-user passwords live in the local seed config. Never echo them or put them in reports or URLs.
- **Every probe that can be automated becomes a regression test** (e.g. `tests/<feature>/test_abuse.*`). A failing
  probe is a bug: failing test first, then the fix (QA step 5).
- **Pre-existing or new?** When a probe fails, re-run it on the base commit.
  - A pre-existing platform weakness is recorded as a finding with an owner and listed under residual risks. It is not
    fixed inside the feature without an ADR and the user's go-ahead.
  - Auth, permissions, access policy, document rendering and upload validation are Full-tier surfaces, so a fix there
    is its own PRD.
- **Critical or High = STOP.** Tell the user in chat before continuing.

## Harnesses

| | Harness | Use for | How |
|---|---|---|---|
| A | **In-process test client** (`<your test command>`) | Tampered requests, CSRF, the permission matrix, uploads, rendering | Build requests directly; assert status, body and stored state |
| B | **Built-in browser** as test users (load the `built-in-browser` skill first) | URL edits, payloads typed into free text, visual escaping, error pages | `javascript_tool` is for inspection only; send tampered bodies through A |
| C | **Local canary** | SSRF | An HTTP server on `127.0.0.1:<ephemeral>` inside the test process. Assert zero hits |

## Test users (define in your local seed)

| User | Probe use |
|---|---|
| `owner` | Positive control; owns the records used in IDOR tests |
| `other-tenant` | Same role, **different tenant/organisation** (IDOR, tenancy) |
| `no-permission` | Authenticated but lacks the feature's permission |
| `admin` | Elevated role; confirm admin-only actions and that "admin can do anything" is intended |
| `no-role` | Authenticated with no role at all (catches fail-open permission checks) |
| `disabled` | Disabled account; login refused |

## Checklist

| ID | Surface | Pass when | If it fails |
|---|---|---|---|
| AB-01 | Tampered derived, hidden or server-only fields | Stored values equal the server's own recomputation | Critical if business-critical; otherwise High |
| AB-02 | Step skipping and resume in multi-step flows | Submit is refused unless every required step's rules hold | High / Medium |
| AB-03 | CSRF | Every new mutating endpoint rejects a missing or wrong token | High |
| AB-04 | IDOR / tenancy | Another tenant's ID gives 404/403, with nothing written | Critical |
| AB-05 | XSS / template injection | Payloads render as literal text everywhere they appear | High (stored) |
| AB-06 | SSRF / local file read | Zero canary hits; no `file://` content returned or rendered | High |
| AB-07 | Uploads | Only allowed types whose bytes match; size and count limits enforced; downloads safe | High / Medium |
| AB-08 | Permission bypass | Every route of the feature enforces its permission for each negative user | High |
| AB-09 | Login, rate limit, session | Limits hold; no user enumeration; session rotates; safe redirects | Medium |
| AB-10 | Sensitive-data exposure | Restricted fields never appear in pages, exports, logs or API responses for users who may not see them | Critical |
| AB-11 | Headers and responses | `no-store` on sensitive pages, `nosniff`, correct content types, no stack traces | Low |

### AB-01 Tampered derived, hidden and server-only fields
**Probes (A)**
1. POST every derived or outcome field (price, status, score, role, owner ID) on each endpoint that accepts input.
2. POST fields belonging to another step or form.
3. Change the inputs a derived value depends on at the final submit, bypassing intermediate steps (stale derivation).
4. Send off-list values for enumerations (radio, select), an empty string and 10 kB of text.
5. Send a value for a field the UI hides in the current state.

**Expected:** stored values match the pure domain function run on the final inputs; off-list values are rejected;
hidden values are cleared or ignored.

### AB-02 Step skipping and resume
**Probes (A+B)**
1. Jump straight to the last step via the URL or a posted step index, with earlier required fields empty.
2. Post out-of-range step values: `0`, `-1`, `999`, `abc`.
3. Put inputs into a state the UI blocks (a hard stop), then submit directly.
4. Save a draft, change a gating input by POST, resume, and submit.

**Expected:** the server re-validates every applicable step on submit and clamps out-of-range steps. What counts as a
hard stop is a domain rule: take it from the spec, never invent it.

### AB-03 CSRF
**Probes (A):** for each new mutating endpoint (form POST, JSON API, upload, delete), send no token, a wrong token, and
another session's token. Each is rejected. No GET changes server state. Never add a feature endpoint to a CSRF
exemption list.

### AB-04 IDOR and tenancy
**Probes (A+B)**
1. As `owner`, create a record (draft, document, job) and note its ID.
2. As `other-tenant`, request it directly by ID, including every related view, download and status endpoint.
3. Repeat for every new ID-keyed route. Sequential IDs make this easy for an attacker; try neighbours too.

**Expected:** 404 (or 403), with nothing from the other tenant written into the session or response.
**Record:** if the step-2 request succeeds on the base commit too, it is a **pre-existing Critical finding**. STOP and
report it; don't patch the platform silently.

### AB-05 XSS and template injection through free text
**Facts to check:** is autoescaping on for every renderer (pages, e-mails, PDFs/exports)? Any raw-HTML escape hatch
(`|safe`, `dangerouslySetInnerHTML`, `v-html`, `innerHTML`, string-built templates) that touches user data? Does the
enforced Content-Security-Policy actually restrict `script-src`?

**Payloads:** `<script>alert(1)</script>`, `"><img src=x onerror=alert(1)>`, `{{7*7}}`, `{% print 7*7 %}`, `${7*7}`,
`javascript:alert(1)` in any URL-like field, and a U+202E RTL override in names.
**Surfaces:** the page that re-renders input, summaries, modals, lists and cards, e-mails, exports, and any JS-built
list. **Expected:** escaped text in HTML, literal text in exports, and `49` never appears. Screenshot one browser case.

### AB-06 SSRF and local file read
**Where:** any server-side fetch of a user-influenced URL: webhooks, URL previews, image proxies, HTML-to-PDF renderers
(which often fetch every `src`/`href`, including `file://`), import-from-URL features.

**Probes (A+C)**
1. Put `<img src="http://127.0.0.1:<canary>/x">`, `http://169.254.169.254/latest/meta-data/` and
   `file:///etc/hostname` into every free-text or URL field and into upload filenames.
2. Trigger the server-side fetch or render.
3. Assert zero canary hits and no host file content in the output.

**Hardening (via an ADR):** an allow-list URL fetcher, no redirects to private ranges.

### AB-07 Uploads
**Probes (A)**
1. PNG bytes named `.pdf`; PDF bytes followed by `<script>`.
2. An SVG or HTML file renamed `.png`.
3. `x.pdf.html`, `../../x.pdf`, a filename with NUL or control characters, a 300-character filename.
4. A zero-byte file.
5. Limit + 1 byte, then 10× the limit (time the response and watch memory: is the size checked before reading?).
6. One file more than the count limit.
7. EICAR, only with a local scanner enabled.

Then download each stored object: served as an attachment with `nosniff` and a safe content type.
**Evidence:** status and error text, the local storage listing, the response headers.

### AB-08 Permission bypass
**Probes (A+B), per negative user:** call **every** route of the feature directly, not just its entry page: reads,
writes, status polling, downloads, and any JSON API. Also:
- revoke the permission mid-session; the next request is denied (watch for over-long permission caches);
- switch role or tenant with the feature open, if the app supports it.

**Expected:** 403 or a redirect for `no-permission`, `other-tenant` (where tenancy applies) and `disabled`.
**`no-role` succeeding is a fail-open.** Record it (pre-existing or new) and never use it as positive evidence.

### AB-09 Login, rate limit, session
**Probes (B, or A with rate limiting enabled)**
1. Repeated wrong passwords for one user → rate-limited response at the configured threshold.
2. Compare messages for an unknown user and a wrong password; they must not differ.
3. The session ID rotates at login.
4. Log out, then replay the old session cookie.
5. Redirect parameters (`next`, `continue`, `returnTo`) = `//evil.test` and `/\evil.test`.
6. New expensive endpoints (exports, uploads, third-party lookups) have a rate limit, or the gap is recorded.

Afterwards reset **only** the local rate-limit store.

### AB-10 Sensitive-data exposure
**Probes (A+B)** with a synthetic record marked restricted/sensitive (or a field a role may not see): run the full
journey. Check pages, API responses, exports, e-mails, logs and error messages for the restricted fields.
If what should be hidden is not defined in the spec, that is a **domain ruling: STOP**.

### AB-11 Headers and responses
- Sensitive pages return `Cache-Control: no-store`.
- `X-Content-Type-Options: nosniff`, a frame policy and a referrer policy are set.
- JSON routes return `application/json`.
- Errors show no stack trace or debug page.
- Exit or "return" URLs stay same-origin.

## Recording (`05-qa-report.md`)

| ID | Probe | User / harness | Expected | Observed (verbatim) | Evidence | Base commit too? | Severity | Status |
|---|---|---|---|---|---|---|---|---|
| AB-04.2 | GET /<resource>/<id of owner's record> as other-tenant | other-tenant / A | 404, nothing written | … | `tests/<feature>/test_abuse.*::test_other_tenant` + summary line | yes/no | Critical | open/fixed/pre-existing→owner |

- **Evidence:** test IDs with the verbatim summary line; screenshots under `.SDD/specs/<slug>/evidence/qa/`; SQL only as
  `SELECT`s on the local DB; canary hit counts; response headers.
- Never paste personal data, cookies, tokens or passwords.
- Rows not applicable to the feature are marked N/A with a reason. Don't delete them.
- Give the **qa-verifier** agent this table and the evidence paths, without your verdicts.
