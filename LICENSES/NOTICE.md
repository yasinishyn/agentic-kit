# Third-party notices

This kit contains material adapted from two MIT-licensed projects, and one vendored MIT-licensed library. Their licence
texts are kept verbatim in this folder; keep them (and this notice) when you copy the kit into a project.
Release builds of the Kanban desktop app also bundle a Python runtime; see the last section.

| Upstream | Commit | Used for | Licence |
|---|---|---|---|
| [obra/superpowers](https://github.com/obra/superpowers) by Jesse Vincent | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` (v6.4.2) | `.claude/skills/test-driven-development/`, `.claude/skills/systematic-debugging/`, `.claude/skills/verification-before-completion/` | MIT, © 2025 Jesse Vincent — [`superpowers-LICENSE`](superpowers-LICENSE) |
| [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) | `053ddbbf392a1688fc7043d81529f47ef2cf86c8` | `.claude/agents/architect.md` (from `engineering/engineering-software-architect.md`), `.claude/agents/qa-verifier.md` (from `testing/testing-evidence-collector.md` and `testing/testing-reality-checker.md`) | MIT, © 2025 AgentLand Contributors — [`agency-agents-LICENSE`](agency-agents-LICENSE) |
| [xtermjs/xterm.js](https://github.com/xtermjs/xterm.js) (`@xterm/xterm`) | 5.5.0 (npm release) | `plugins/kanban/ui/vendor/xterm/` (`xterm.js`, `xterm.css`: the Kanban app's terminal) | MIT, © The xterm.js authors — [`xterm-LICENSE`](xterm-LICENSE) |

The adapted files are rewrites, not verbatim copies: the method is kept, and the wording, examples and output formats
were changed to fit this kit. Each adapted file names its source in a comment at the top. The xterm.js files are
vendored unmodified; their version and checksums are in `plugins/kanban/ui/vendor/xterm/VERSION`.

## Runtime bundled in the Kanban app

Release builds of the Kanban desktop app (`Kanban.app`) bundle a Python runtime, one per architecture, pinned in
`plugins/kanban/app/python.lock`. It ships **inside the app only**: it is not part of the kit's source tree and it is
not copied into projects by `install.py`. Its licence file is kept in the app (the build never prunes it; a test checks
it).

| Upstream | Version | Used for | Licence |
|---|---|---|---|
| [CPython](https://www.python.org/) by the Python Software Foundation | 3.12.14 | the interpreter and standard library under `Kanban.app/Contents/Resources/python/<arch>/` (runs the board daemon and, through `scripts/kpython`, the plugin) | PSF License Agreement (Python Software Foundation License Version 2) and the other terms in the same file — `Kanban.app/Contents/Resources/python/arm64/lib/python3.12/LICENSE.txt`, `Kanban.app/Contents/Resources/python/x86_64/lib/python3.12/LICENSE.txt` |
| [astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone) | release `20260924`, `install_only_stripped` (`aarch64-apple-darwin`, `x86_64-apple-darwin`) | the relocatable build of that CPython | build scripts: BSD 3-Clause; the distribution's terms are those of its components, see below |

The python-build-standalone distribution links third-party libraries statically into the interpreter and its extension
modules (among them OpenSSL, SQLite, libffi, zlib, bzip2, xz/liblzma, mpdecimal, expat, ncurses and libedit).
The `install_only_stripped` archive carries only CPython's `LICENSE.txt`, listed above; the licences of
the statically linked components are documented by the project at
<https://gregoryszorc.com/docs/python-build-standalone/main/running.html#licensing> and recorded per component in the
`PYTHON.json` of its full archives.

Everything else in the kit is original to the kit.
