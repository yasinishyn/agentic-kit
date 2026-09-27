export const meta = {
  name: 'sdd-streams',
  description: 'Run approved SDD PRD streams in parallel worktrees: implement (TDD) -> self-verify gates -> read-only review',
  whenToUse: 'Only after the user approved the spec and asked to execute, with the spec folder and .claude/ committed on the feature branch',
  phases: [
    { title: 'Implement', detail: 'one agent per PRD in its own worktree; TDD; commits on the stream branch' },
    { title: 'Verify', detail: 'fresh agent re-runs the PRD gates and lists changed files' },
    { title: 'Review', detail: 'read-only review of the stream diff against its PRD' },
  ],
}

// SDD parallel streams (see parallel-work.md). Plain JS, deterministic (no clock/random) so resume works.
// Launch from the main thread:
//   Workflow({ scriptPath: '<repo>/.claude/skills/sdd/reference/streams.workflow.js', args })
// args = {
//   repo: '/abs/path/to/repo',                    // main checkout: streams never write here
//   featureBranch: 'feature/my-feature',          // base of every stream branch '<featureBranch>--<id>'
//   specDir: '.SDD/specs/<slug>',                 // repo-relative
//   reviewerAgentType: 'qa-verifier',             // optional; omit if not registered (the prompt then points at .claude/agents/qa-verifier.md)
//   streams: [{ id: 'p01',                        // [a-z0-9]+ (may be used in host, DB and container names)
//     prd: 'prd/PRD-01-<title>.md',               // relative to specDir
//     owned: ['src/pricing/rules.py', 'tests/pricing/test_rules.py'],   // exact paths, 'dir/' or globs (* **)
//     forbidden: ['migrations/', 'src/routes.py'],
//     gates: ['cd {WT} && <your test command> tests/pricing'],          // {WT} -> worktree path
//     worktree: '/abs/path/to/repo/.claude/worktrees/p01' }],           // optional: pre-created + provisioned by the orchestrator
// }
// Without `worktree`, the implementer runs with isolation:'worktree' (fresh worktree from the main checkout's
// HEAD, which must be featureBranch). Reports are CLAIMS: the orchestrator re-checks the diffs and re-runs the gates.

const A = args || {}
if (!A.repo || !A.featureBranch || !A.specDir || !Array.isArray(A.streams) || A.streams.length === 0) {
  throw new Error('args must be { repo, featureBranch, specDir, streams: [...] } (see header comment)')
}

// ---- Schemas -----------------------------------------------------------------------------------------
const list = { type: 'array', items: { type: 'string' } }
const IMPL = { type: 'object', required: ['status', 'worktree', 'branch', 'headSha', 'filesChanged', 'testsSummary', 'blockers'],
  properties: { status: { type: 'string', enum: ['DONE', 'DONE_WITH_CONCERNS', 'BLOCKED', 'NEEDS_CONTEXT'] },
    worktree: { type: 'string' }, branch: { type: 'string' }, headSha: { type: 'string' }, commits: list,
    filesChanged: list, testsAdded: list, testsSummary: { type: 'string' }, blockers: list, notes: { type: 'string' } } }
// The stream report returned by self-verify (what the orchestrator acts on).
const STREAM_REPORT = { type: 'object', required: ['passed', 'testsSummary', 'filesChanged', 'blockers'],
  properties: {
    passed: { type: 'boolean' },              // every gate exit 0 AND nothing uncommitted
    testsSummary: { type: 'string' },         // verbatim test-runner summary line(s)
    filesChanged: list,                       // git diff --name-only <featureBranch>...HEAD
    blockers: list, uncommitted: list,
    gates: { type: 'array', items: { type: 'object', required: ['command', 'exitCode', 'summary'],
      properties: { command: { type: 'string' }, exitCode: { type: 'integer' }, summary: { type: 'string' } } } } } }
const REVIEW = { type: 'object', required: ['verdict', 'findings'],
  properties: { verdict: { type: 'string', enum: ['READY', 'NEEDS_WORK'] },
    findings: { type: 'array', items: { type: 'object', required: ['severity', 'summary'],
      properties: { severity: { type: 'string', enum: ['Critical', 'High', 'Medium', 'Low'] },
        file: { type: 'string' }, line: { type: 'integer' }, criterion: { type: 'string' }, summary: { type: 'string' } } } } } }

// ---- Helpers -----------------------------------------------------------------------------------------
const bullets = (xs) => (xs && xs.length ? xs.map((x) => `  - ${x}`).join('\n') : '  - (none)')
const branchOf = (s) => `${A.featureBranch}--${s.id}`
const fill = (xs, wt) => (xs || []).map((x) => (wt ? x.split('{WT}').join(wt) : x))  // substitute the worktree path
const globRe = (g) => new RegExp('^' + g.replace(/[.+^${}()|[\]\\?]/g, '\\$&').replace(/\*\*/g, '\u0000')
  .replace(/\*/g, '[^/]*').replace(/\u0000/g, '.*') + (g.endsWith('/') ? '.*' : '') + '$')
const matches = (f, globs) => (globs || []).some((g) => globRe(g).test(f))

function implementPrompt(s) {
  const where = s.worktree
    ? `WT = ${s.worktree} (pre-created on branch ${branchOf(s)}, stream resources provisioned).`
    : `You start in a fresh git worktree. First Bash call: git rev-parse --show-toplevel -> WT (it must NOT be ${A.repo}); then git -C "$WT" branch -m ${branchOf(s)}.`
  return `You implement ONE approved PRD of the spec ${A.specDir}, as stream "${s.id}". ${where}
Read first: WT/${A.specDir}/${s.prd}, the ADRs it cites, and WT/.claude/skills/sdd/reference/stream-brief.md ("Rules", "Stop conditions").
Owned files (the ONLY paths you may create or change):
${bullets(s.owned)}
Forbidden: the whole ${A.specDir}/ folder (orchestrator-owned), the main checkout ${A.repo}, other worktrees, and:
${bullets(s.forbidden)}
Gates (run exactly, with any {WT} replaced by your absolute WT; Bash cwd resets between calls, so every path is absolute):
${bullets(fill(s.gates, s.worktree))}
Method: skills test-driven-development (see RED fail for the right reason, then GREEN) and systematic-debugging on any
failure (both in WT/.claude/skills/). Commit on ${branchOf(s)} only:
git -C WT add <owned paths> && git -C WT commit -m "<PRD id>: <summary> (<FR ids>)". Never push, never use a non-local
service or database or real personal data, never run git add -A.
Return status BLOCKED (blockers = exact questions) for any domain ruling the spec leaves open (thresholds, pricing,
eligibility, regulated wording, routing), for a needed change outside the owned files, or after 3 failed fix attempts.
Report: worktree (absolute WT), branch, headSha, commits (oneline), filesChanged (git -C WT diff --name-only ${A.featureBranch}...HEAD),
testsAdded, testsSummary (verbatim test-runner summary), blockers, notes.`
}

function verifyPrompt(impl, s) {
  return `Independently verify stream "${s.id}" of ${A.specDir}. Do NOT edit, stage, commit or fix anything.
Worktree ${impl.worktree} (branch ${impl.branch}); base ${A.featureBranch}. Bash cwd resets: absolute paths / git -C only.
1. git -C ${impl.worktree} status --porcelain  -> any path listed goes in "uncommitted".
2. git -C ${impl.worktree} diff --name-only ${A.featureBranch}...HEAD  -> filesChanged (exact list).
3. Run every gate exactly, in order; record command, exit code and the verbatim summary line:
${bullets(fill(s.gates, impl.worktree))}
passed = all gates exit 0 AND uncommitted is empty. blockers = anything that stopped a gate running (stream resources
missing, a service down, ...). Evidence only, no opinions.`
}

function reviewPrompt(impl, s) {
  const persona = A.reviewerAgentType ? '' : `First Read ${impl.worktree}/.claude/agents/qa-verifier.md if it exists and apply its stance.\n`
  return `${persona}Read-only review of stream "${s.id}" against ${impl.worktree}/${A.specDir}/${s.prd} and its ADRs.
Do not edit files, run tests or change git state. Diff: git -C ${impl.worktree} diff ${A.featureBranch}...HEAD
Check: each acceptance criterion implemented AND tested; tests assert literal expected values (not mocks, not recomputed);
derived or business-critical values recomputed server-side (never trusted from posted or hidden fields); no domain
decision taken that the spec leaves open; CSRF, authorisation and route registration for new endpoints; no bypass of
output escaping with user data; synthetic data only; nothing outside the owned list ${JSON.stringify(s.owned)}.
Findings: severity Critical/High/Medium/Low, file, line, criterion, one-sentence summary. verdict READY only if no Critical/High.`
}

// ---- Pipeline: each stream flows implement -> verify -> review independently (no barrier) -------------
const results = await pipeline(
  A.streams,
  async (s) => {
    const opts = { label: `implement ${s.id}`, phase: 'Implement', schema: IMPL }
    if (!s.worktree) opts.isolation = 'worktree'
    return { impl: await agent(implementPrompt(s), opts) }
  },
  async (r, s) => {
    const ok = r.impl && (r.impl.status === 'DONE' || r.impl.status === 'DONE_WITH_CONCERNS')
    if (!ok) return { ...r, verify: null }
    return { ...r, verify: await agent(verifyPrompt(r.impl, s), { label: `verify ${s.id}`, phase: 'Verify', schema: STREAM_REPORT }) }
  },
  async (r, s) => {
    if (!r.verify) return { ...r, review: null }
    const opts = { label: `review ${s.id}`, phase: 'Review', schema: REVIEW }
    if (A.reviewerAgentType) opts.agentType = A.reviewerAgentType
    return { ...r, review: await agent(reviewPrompt(r.impl, s), opts) }
  },
)

// ---- Per-stream results for the orchestrator (it still re-checks everything itself) ------------------
const streams = A.streams.map((s, i) => {
  const r = results[i] || {}
  const impl = r.impl || null, verify = r.verify || null, review = r.review || null
  const changed = (verify && verify.filesChanged) || (impl && impl.filesChanged) || []
  const outOfScope = changed.filter((f) => !matches(f, s.owned))
  const forbiddenHits = changed.filter((f) => matches(f, s.forbidden))
  const blocking = review ? review.findings.filter((f) => f.severity === 'Critical' || f.severity === 'High').length : null
  const ready = !!(verify && verify.passed && !outOfScope.length && !forbiddenHits.length && review && review.verdict === 'READY')
  log(`${s.id}: ${ready ? 'candidate for merge' : 'NOT ready'} | impl=${impl ? impl.status : 'none'} gates=${verify ? verify.passed : 'n/a'} outOfScope=${outOfScope.length} blocking=${blocking}`)
  return { id: s.id, prd: s.prd, status: impl ? impl.status : 'NO_RESULT', ready, branch: impl && impl.branch,
    worktree: impl && impl.worktree, headSha: impl && impl.headSha, outOfScope, forbiddenHits, impl, verify, review }
})
return { featureBranch: A.featureBranch, specDir: A.specDir, streams }
