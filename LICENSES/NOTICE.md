# Third-party notices

This kit contains material adapted from two MIT-licensed projects, and one vendored MIT-licensed library. Their licence
texts are kept verbatim in this folder; keep them (and this notice) when you copy the kit into a project.

| Upstream | Commit | Used for | Licence |
|---|---|---|---|
| [obra/superpowers](https://github.com/obra/superpowers) by Jesse Vincent | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` (v6.4.2) | `.claude/skills/test-driven-development/`, `.claude/skills/systematic-debugging/`, `.claude/skills/verification-before-completion/` | MIT, © 2025 Jesse Vincent — [`superpowers-LICENSE`](superpowers-LICENSE) |
| [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) | `053ddbbf392a1688fc7043d81529f47ef2cf86c8` | `.claude/agents/architect.md` (from `engineering/engineering-software-architect.md`), `.claude/agents/qa-verifier.md` (from `testing/testing-evidence-collector.md` and `testing/testing-reality-checker.md`) | MIT, © 2025 AgentLand Contributors — [`agency-agents-LICENSE`](agency-agents-LICENSE) |
| [xtermjs/xterm.js](https://github.com/xtermjs/xterm.js) (`@xterm/xterm`) | 5.5.0 (npm release) | `plugins/kanban/ui/vendor/xterm/` (`xterm.js`, `xterm.css`: the Kanban app's terminal) | MIT, © The xterm.js authors — [`xterm-LICENSE`](xterm-LICENSE) |

The adapted files are rewrites, not verbatim copies: the method is kept, and the wording, examples and output formats
were changed to fit this kit. Each adapted file names its source in a comment at the top. The xterm.js files are
vendored unmodified; their version and checksums are in `plugins/kanban/ui/vendor/xterm/VERSION`.

Everything else in the kit is original to the kit.
