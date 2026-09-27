# agentic-kit

A project-neutral starter kit for **Claude Code**. It gives any repository:

- **a light spec-driven delivery flow (SDD):** Discovery → Architect → your approval → Developer → QA → Demo → E2E;
- **engineering discipline:** test-driven development, systematic debugging, evidence before "done";
- **two review agents;**
- **guardrails** that stop agents from pushing, deploying, calling cloud CLIs or reading credentials;
- **optionally, a kanban board** over your spec markdown: tickets are spec folders, and the columns are the ADLC stages.

You pick the parts you want. The installer **only adds**: it never overwrites your files, skills, agents or SDD setup.

---

## Installation

**Requirements:**
- `git`;
- `bash`;
- `python3` 3.9 or newer: the installer, the guard hook and the kanban plugin use only the standard library;
- the `claude` CLI, only for the kanban plugin.

For a **private** repository you need GitHub access set up first, either an SSH key or a personal access token.
Use the SSH URL (`git@github.com:<you>/agentic-kit.git`) in the commands below if that's how you authenticate.

### 1. Get the kit from GitHub

**First time:** clone it once, anywhere outside your projects.

```bash
git clone https://github.com/<you>/agentic-kit.git ~/agentic-kit
```

**Already cloned:** pull the latest version first.

```bash
git -C ~/agentic-kit pull
```

### 2. Preview what will change

```bash
~/agentic-kit/install.sh /path/to/your/project --dry-run --all
```

Nothing is written. Every line says `added`, `kept (yours)`, `skipped: …` or `merged …`, so you can see that none of your
files will be overwritten.

### 3. Install into your project

```bash
~/agentic-kit/install.sh /path/to/your/project
```

It asks, component by component: `sdd`, `skills`, `agents`, `guard`, `instructions`, `kanban`. To skip the questions:

```bash
~/agentic-kit/install.sh /path/to/your/project --only sdd,skills,agents --yes
```

### 4. Optional: the kanban board plugin

Choosing `kanban` in step 3 runs these for you. To do it by hand:

```bash
claude plugin marketplace add <you>/agentic-kit
```

```bash
claude plugin install kanban@agentic-kit
```

Claude Code keeps its own copy of the marketplace, so the plugin updates separately from your clone (see "Updating").

### 5. Finish in the project

1. Fill in the placeholders: `grep -rn '<your ' CLAUDE.md .claude .SDD`.
2. If you installed `guard`, add project rules to the CONFIG block in `.claude/hooks/guard_bash.py`, then run
   `bash .claude/hooks/test_guard_bash.sh`.
3. Start Claude Code in the project's root folder, or restart it. If you installed the plugin, `/mcp` should list
   `plugin:kanban:kanban`.
4. Review the added files (`git status`) and commit them on a feature branch. The kit never pushes; you do.

## Components

| Component | What you get | Where it goes |
|---|---|---|
| `sdd` | The `sdd` skill plus the process README, spec templates and a `specs/` folder | `.claude/skills/sdd/`, `.SDD/` |
| `skills` | `test-driven-development`, `systematic-debugging`, `verification-before-completion` | `.claude/skills/` |
| `agents` | `architect` (independent design review), `qa-verifier` (evidence-based QA verdict) | `.claude/agents/` |
| `guard` | A PreToolUse hook that blocks `git push`, deploy scripts, the AWS CLI, non-local database clients and credential reads; deny rules added to your settings | `.claude/hooks/`, `.claude/settings.json` |
| `instructions` | A `CLAUDE.md` template (hard rules, how we work) and a long-term memory index | `CLAUDE.md`, `.claude/memory/` |
| `kanban` | A Claude Code plugin: a board over `.SDD/specs` (tickets = spec folders, columns = stages), MCP tools, a `ticket` kick-off skill | installed with `claude plugin …` (see below) |

All of them except `kanban` are selected by default.

## Install options

```bash
./install.sh <project>                          # interactive
./install.sh <project> --only sdd,skills,agents  # pick components without questions
./install.sh <project> --all --yes              # everything, no questions
./install.sh <project> --dry-run                # show what would happen; change nothing
./install.sh <project> --update                 # after `git pull`: refresh kit files you haven't edited
./install.sh --list                             # list the components
./install.sh <project> --only kanban --kanban-scope project   # enable the plugin for everyone in that repo
```

### What "never overwrites" means exactly

| Situation | What the installer does |
|---|---|
| A file already exists | Kept as it is. |
| A skill or agent with the same name exists (e.g. your own `sdd`) | That whole skill or agent is skipped; the kit never mixes files into yours. |
| The project already has `.SDD/` | Only missing templates are added. Your README, templates and specs are untouched. |
| `CLAUDE.md` exists | Left alone. The kit text goes to `.claude/agentic-kit/CLAUDE.kit.md`; add `@.claude/agentic-kit/CLAUDE.kit.md` to your `CLAUDE.md` if you want it. |
| `.claude/settings.json` exists | **Merged by adding only:** missing deny/ask rules and the guard hook. Your values stay. A backup `settings.json.bak-<time>` is written first. |
| The project already has its own Bash guard hook | The kit's guard is skipped, so you don't get two competing guards. |
| `.claude/skills` is a symlink | Respected: files are added inside the linked folder, following the same rules. |

Each install is recorded in `.claude/agentic-kit/installed.json`: which components, which files, and the hash of each file.

## Updating

**The kit files in a project:**

```bash
git -C ~/agentic-kit pull
~/agentic-kit/install.sh /path/to/your/project --update --dry-run   # preview
~/agentic-kit/install.sh /path/to/your/project --update
```

- **Refreshed:** a kit file you haven't touched, i.e. one still identical to what the kit installed. The hashes are in `.claude/agentic-kit/installed.json`.
- **Kept:** a file you edited. The run reports it, and you can compare it with the kit's version yourself.
- **Added:** new files that appeared in the kit.

**The kanban plugin:** Claude Code updates it from GitHub, not from your clone.

```bash
claude plugin marketplace update agentic-kit
```

```bash
claude plugin update kanban@agentic-kit
```

Then restart Claude Code.

## The kanban plugin

A board over your `.SDD/specs` markdown:
- **Ticket:** a spec folder `.SDD/specs/<slug>/`.
- **Columns:** the ADLC stages (Discovery · Architect · Approval · Developer · QA · Demo · E2E · Done).
- **Stage:** `status:` in the ticket README's frontmatter.
- **Sub-tasks:** the PRD files (and optional `tasks/`), each with its own `status:`. Their checkboxes are the steps.
- **Files:** every markdown file opens from the board, in a viewer or in your editor.

The markdown is the source of truth: the `sdd` skill keeps it current, and the plugin displays and edits it. A prompt
becomes a ticket only when it produces a spec; the `ticket` skill (`/kanban:ticket <title>`) kicks off SDD for it.

**Install it** (it's a per-user Claude Code plugin; this repository is its marketplace):

```bash
claude plugin marketplace add <you>/agentic-kit     # or a local path: ~/agentic-kit
claude plugin install kanban@agentic-kit
```

or `./install.sh <project> --only kanban`, which runs the same two commands.

**Share it with a team:** use `--kanban-scope project`, or commit this to the project's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": { "agentic-kit": { "source": { "source": "github", "repo": "<you>/agentic-kit" } } },
  "enabledPlugins": { "kanban@agentic-kit": true }
}
```

Details: [plugins/kanban/README.md](plugins/kanban/README.md).

## Using it day to day

- **Start a feature:** "spec this: …" or `/sdd …`. Claude runs Discovery and Architect, writes `.SDD/specs/<slug>/`, and **stops for your approval**. Say **"execute"** to start the build.
- **Small fixes:** the `sdd` skill tiers them down to Developer → QA.
- **Review agents:** Claude uses `architect` and `qa-verifier` at the Architect and QA stages. If your Claude Code doesn't register custom agents, the skill tells Claude to read `.claude/agents/<name>.md` instead.

## Removing it

Delete the paths listed in `.claude/agentic-kit/installed.json`, and restore `.claude/settings.json` from its `.bak-…` file if you want the deny rules and hook gone. For the kanban plugin, run `claude plugin uninstall kanban@agentic-kit`.

## For maintainers

**Publish to GitHub** (the kit never pushes; these are your commands):

1. Create an **empty** repository named `agentic-kit` on github.com: no README, no licence, no .gitignore.
2. Push:
   ```bash
   git -C ~/agentic-kit remote add origin https://github.com/<you>/agentic-kit.git
   git -C ~/agentic-kit push -u origin main
   ```

**Tests:**

```bash
python3 tests/test_install.py
python3 plugins/kanban/tests/test_kanban.py
claude plugin validate plugins/kanban && claude plugin validate .
bash .claude/hooks/test_guard_bash.sh
```

**Layout:**

```
agentic-kit/
├── install.sh / install.py      installer (additive only)
├── CLAUDE.md                    instructions template
├── .claude/                     skills, agents, hooks, settings, memory (the installable parts)
├── .SDD/                        process README + spec templates
├── .claude-plugin/marketplace.json
├── plugins/kanban/              the kanban plugin (hooks, MCP server, web board, skill, tests)
├── tests/                       installer tests
└── LICENSES/                    upstream licences
```

## Licences

Parts of the skills and agents are adapted from MIT-licensed projects:
[obra/superpowers](https://github.com/obra/superpowers) by Jesse Vincent and
[msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents). See [LICENSES/NOTICE.md](LICENSES/NOTICE.md).
The kit's own licence is still to be chosen: add a `LICENSE` file before publishing publicly.
