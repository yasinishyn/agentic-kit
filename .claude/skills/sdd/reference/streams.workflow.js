export const meta = {
  name: 'sdd-streams',
  description: 'Run approved SDD PRD streams in parallel as subagents on disjoint owned files: implement (TDD) -> self-verify gates -> read-only review',
  whenToUse: 'Only after the user approved the spec and asked to execute, with disjoint owned files per stream',
  phases: [
    { title: 'Implement', detail: 'one agent per PRD, same checkout, owned files only; TDD; no staging or commits (default)' },
    { title: 'Verify', detail: 'fresh agent re-runs the PRD gates and lists the changed owned files' },
    { title: 'Review', detail: 'read-only review of the stream changes against its PRD' },
  ],
}

// SDD parallel streams (see parallel-work.md). Plain JS, deterministic (no clock/random) so resume works.
// Launch from the main thread:
//   Workflow({ scriptPath: '<repo>/.claude/skills/sdd/reference/streams.workflow.js', args })
// args = {
//   repo: '/abs/path/to/repo',                    // the developer's checkout (default mode: every stream writes here)
//   featureBranch: 'feature/my-feature',          // the branch the developer has checked out (opt-in mode: base of '<featureBranch>--<id>')
//   specDir: '.SDD/specs/<slug>',                 // repo-relative
//   reviewerAgentType: 'qa-verifier',             // optional; omit if not registered (the prompt then points at .claude/agents/qa-verifier.md)
//   preexisting: ['README.md'],                   // optional: paths already dirty before the wave (not counted as strays)
//   allowAgentCommits: false,                     // default false. true ONLY if the project set ALLOW_AGENT_COMMITS = True
//                                                 // in .claude/hooks/guard_bash.py: worktrees, stream branches, commits as the developer
//   streams: [{ id: 'p01',                        // [a-z0-9]+ (may be used in host, DB and container names)
//     prd: 'prd/PRD-01-<title>.md',               // relative to specDir
//     owned: ['src/pricing/rules.py', 'tests/pricing/test_rules.py'],   // exact paths, 'dir/' or globs (* **)
//     forbidden: ['migrations/', 'src/routes.py'],
//     gates: ['cd {WT} && <your test command> tests/pricing'],          // {WT} -> the checkout (or worktree) path
//     worktree: '/abs/path/to/repo/.claude/worktrees/p01' }],           // opt-in mode only: pre-created + provisioned
// }
// Default: every stream is a subagent in the same checkout, writing only its owned files, and never stages, commits,
// branches or creates worktrees; the orchestrator checks scope with read-only git and stages with `git add`.
// Reports are CLAIMS: the orchestrator re-checks the changed files and re-runs the gates.

const A = args || {}
if (!A.repo || !A.featureBranch || !A.specDir || !Array.isArray(A.streams) || A.streams.length === 0) {
  throw new Error('args must be { repo, featureBranch, specDir, streams: [...] } (see header comment)')
}
const COMMITS = A.allowAgentCommits === true
if (!COMMITS && A.streams.some((s) => s.worktree)) {
  throw new Error('streams[].worktree needs allowAgentCommits: true (the project opted in via ALLOW_AGENT_COMMITS)')
}

// ---- Schemas -----------------------------------------------------------------------------------------
const list = { type: 'array', items: { type: 'string' } }
const IMPL = { type: 'object', required: ['status', 'filesChanged', 'testsSummary', 'blockers'],
  properties: { status: { type: 'string', enum: ['DONE', 'DONE_WITH_CONCERNS', 'BLOCKED', 'NEEDS_CONTEXT'] },
    worktree: { type: 'string' }, branch: { type: 'string' }, headSha: { type: 'string' }, commits: list,
    filesChanged: list, testsAdded: list, testsSummary: { type: 'string' }, suggestedCommitMessage: { type: 'string' },
    blockers: list, notes: { type: 'string' } } }
// The stream report returned by self-verify (what the orchestrator acts on).
const STREAM_REPORT = { type: 'object', required: ['passed', 'testsSummary', 'filesChanged', 'allChanged', 'blockers'],
  properties: {
    passed: { type: 'boolean' },              // every gate exit 0 (opt-in mode: AND nothing uncommitted)
    testsSummary: { type: 'string' },         // verbatim test-runner summary line(s)
    filesChanged: list,                       // default: status restricted to owned; opt-in: diff <featureBranch>...HEAD
    allChanged: list,                         // default: every path in `git status --porcelain --untracked-files=all`
    diffStat: { type: 'string' },             // default: last line of `git diff --stat -- <owned>`
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
const fill = (xs, wt) => (xs || []).map((x) => (wt ? x.split('{WT}').join(wt) : x))  // substitute the checkout path
const globRe = (g) => new RegExp('^' + g.replace(/[.+^${}()|[\]\\?]/g, '\\$&').replace(/\*\*/g, '\u0000')
  .replace(/\*/g, '[^/]*').replace(/\u0000/g, '.*') + (g.endsWith('/') ? '.*' : '') + '$')
const matches = (f, globs) => (globs || []).some((g) => globRe(g).test(f))
const allOwned = A.streams.flatMap((s) => s.owned || [])
const pathspec = (s) => (s.owned || []).map((p) => `'${p}'`).join(' ')
const checkoutOf = (impl, s) => (COMMITS ? (impl && impl.worktree) || s.worktree : A.repo)

const RULES = `Read ${A.repo}/.claude/skills/sdd/reference/stream-brief.md ("Rules", "Stop conditions") first.`
const STOP = `Return status BLOCKED (blockers = exact questions) for any domain ruling the spec leaves open (thresholds, pricing,
eligibility, regulated wording, routing), for a needed change outside the owned files, or after 3 failed fix attempts.`

function implementPrompt(s) {
  if (!COMMITS) {
    return `You implement ONE approved PRD of the spec ${A.specDir}, as stream "${s.id}". You work in the developer's checkout
WT = ${A.repo}, alongside other streams that own other files. ${RULES}
Read: WT/${A.specDir}/${s.prd} and the ADRs it cites.
Owned files (the ONLY paths you may create or change):
${bullets(s.owned)}
Forbidden: the whole ${A.specDir}/ folder (orchestrator-owned), every file owned by another stream, and:
${bullets(s.forbidden)}
Gates (run exactly; Bash cwd resets between calls, so every path is absolute):
${bullets(fill(s.gates, A.repo))}
Method: skills test-driven-development (see RED fail for the right reason, then GREEN) and systematic-debugging on any
failure (both in WT/.claude/skills/).
Git: read-only only (status, diff, log). Do NOT git add, commit, stash, checkout, switch, branch, reset or create
worktrees: the index is shared with the other streams, and the developer commits. Never use a non-local service or
database or real personal data. If a gate fails only because of another stream's in-progress file, say so in notes.
${STOP}
Report: filesChanged (git -C ${A.repo} status --porcelain --untracked-files=all -- ${pathspec(s)}), testsAdded,
testsSummary (verbatim test-runner summary), suggestedCommitMessage ("<PRD id>: <summary> (<FR ids>)"), blockers, notes.`
  }
  const where = s.worktree
    ? `WT = ${s.worktree} (pre-created on branch ${branchOf(s)}, stream resources provisioned).`
    : `You start in a fresh git worktree. First Bash call: git rev-parse --show-toplevel -> WT (it must NOT be ${A.repo}); then git -C "$WT" branch -m ${branchOf(s)}.`
  return `You implement ONE approved PRD of the spec ${A.specDir}, as stream "${s.id}". ${where}
The project opted in to agent commits (ALLOW_AGENT_COMMITS). Read WT/.claude/skills/sdd/reference/stream-brief.md ("Rules", "Stop conditions") first.
Read: WT/${A.specDir}/${s.prd} and the ADRs it cites.
Owned files (the ONLY paths you may create or change):
${bullets(s.owned)}
Forbidden: the whole ${A.specDir}/ folder (orchestrator-owned), the main checkout ${A.repo}, other worktrees, and:
${bullets(s.forbidden)}
Gates (run exactly, with any {WT} replaced by your absolute WT; Bash cwd resets between calls, so every path is absolute):
${bullets(fill(s.gates, s.worktree))}
Method: skills test-driven-development (see RED fail for the right reason, then GREEN) and systematic-debugging on any
failure (both in WT/.claude/skills/). Commit on ${branchOf(s)} only, as the developer's configured git identity:
git -C WT add <owned paths> && git -C WT commit -m "<PRD id>: <summary> (<FR ids>)". No Claude/AI author, committer or
Co-Authored-By trailer; never change git config user.*. Never push, never use a non-local service or database or real
personal data, never run git add -A.
${STOP}
Report: worktree (absolute WT), branch, headSha, commits (oneline), filesChanged (git -C WT diff --name-only ${A.featureBranch}...HEAD),
testsAdded, testsSummary (verbatim test-runner summary), blockers, notes.`
}

function verifyPrompt(impl, s) {
  if (!COMMITS) {
    return `Independently verify stream "${s.id}" of ${A.specDir}. Do NOT edit, stage, commit or fix anything.
Checkout ${A.repo} (shared with other streams). Bash cwd resets: absolute paths / git -C only.
1. git -C ${A.repo} status --porcelain --untracked-files=all -- ${pathspec(s)}  -> filesChanged (paths only, exact list).
2. git -C ${A.repo} diff --stat -- ${pathspec(s)}  -> diffStat (its verbatim last line).
3. git -C ${A.repo} status --porcelain --untracked-files=all  -> allChanged (every path, exact list).
4. Run every gate exactly, in order; record command, exit code and the verbatim summary line:
${bullets(fill(s.gates, A.repo))}
passed = all gates exit 0. blockers = anything that stopped a gate running (a service down, another stream's file
breaking the import, ...). Evidence only, no opinions.`
  }
  const wt = checkoutOf(impl, s)
  return `Independently verify stream "${s.id}" of ${A.specDir}. Do NOT edit, stage, commit or fix anything.
Worktree ${wt} (branch ${impl.branch}); base ${A.featureBranch}. Bash cwd resets: absolute paths / git -C only.
1. git -C ${wt} status --porcelain  -> any path listed goes in "uncommitted".
2. git -C ${wt} diff --name-only ${A.featureBranch}...HEAD  -> filesChanged and allChanged (the same exact list).
3. Run every gate exactly, in order; record command, exit code and the verbatim summary line:
${bullets(fill(s.gates, wt))}
passed = all gates exit 0 AND uncommitted is empty. blockers = anything that stopped a gate running (stream resources
missing, a service down, ...). Evidence only, no opinions.`
}

function reviewPrompt(impl, s) {
  const wt = checkoutOf(impl, s)
  const persona = A.reviewerAgentType ? '' : `First Read ${wt}/.claude/agents/qa-verifier.md if it exists and apply its stance.\n`
  const diff = COMMITS
    ? `git -C ${wt} diff ${A.featureBranch}...HEAD`
    : `git -C ${wt} diff -- ${pathspec(s)} and Read any untracked owned file listed by git -C ${wt} status --porcelain --untracked-files=all -- ${pathspec(s)}`
  return `${persona}Read-only review of stream "${s.id}" against ${wt}/${A.specDir}/${s.prd} and its ADRs.
Do not edit files, run tests or change git state (no add, commit, stash or checkout). Changes: ${diff}
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
    if (COMMITS && !s.worktree) opts.isolation = 'worktree'  // worktrees only when the project opted in to agent commits
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
const strays = new Set()
const streams = A.streams.map((s, i) => {
  const r = results[i] || {}
  const impl = r.impl || null, verify = r.verify || null, review = r.review || null
  const changed = (verify && verify.filesChanged) || (impl && impl.filesChanged) || []
  const outOfScope = changed.filter((f) => !matches(f, s.owned))
  const forbiddenHits = changed.filter((f) => matches(f, s.forbidden))
  // Default mode: a changed path that no stream owns (and was not dirty before the wave) cannot be attributed to a stream.
  if (!COMMITS && verify) {
    (verify.allChanged || []).filter((f) => !matches(f, allOwned) && !(A.preexisting || []).includes(f)).forEach((f) => strays.add(f))
  }
  const blocking = review ? review.findings.filter((f) => f.severity === 'Critical' || f.severity === 'High').length : null
  const ready = !!(verify && verify.passed && !outOfScope.length && !forbiddenHits.length && review && review.verdict === 'READY')
  log(`${s.id}: ${ready ? (COMMITS ? 'candidate for merge' : 'candidate for staging') : 'NOT ready'} | impl=${impl ? impl.status : 'none'} gates=${verify ? verify.passed : 'n/a'} outOfScope=${outOfScope.length} blocking=${blocking}`)
  return { id: s.id, prd: s.prd, status: impl ? impl.status : 'NO_RESULT', ready,
    suggestedCommitMessage: impl && impl.suggestedCommitMessage, branch: COMMITS ? impl && impl.branch : A.featureBranch,
    worktree: COMMITS ? impl && impl.worktree : A.repo, headSha: impl && impl.headSha, outOfScope, forbiddenHits, impl, verify, review }
})
if (strays.size) log(`strays (changed, owned by no stream): ${[...strays].join(', ')}`)
return { featureBranch: A.featureBranch, specDir: A.specDir, allowAgentCommits: COMMITS, strays: [...strays], streams }
