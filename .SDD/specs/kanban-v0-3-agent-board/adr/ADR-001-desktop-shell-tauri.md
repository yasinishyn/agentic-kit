# ADR-001: Desktop shell: Tauri 2 with a Python daemon

| Field | Value |
|---|---|
| Status | Accepted — Decided by kit owner, 2026-09-28 (OQ Q01) |
| Date | 2026-09-28 |
| Deciders | kit owner (product); architect (technical) |
| Spec | kanban-v0-3-agent-board |

## Context
The board must be a real macOS desktop app with an embedded database and tools (transcript, terminal, editor, git panel), while the plugin runtime stays Python standard library (KB3-NFR-01).

## Options
| Option | For | Against |
|---|---|---|
| **A. Tauri 2 shell + Python daemon (chosen)** | ~10 MB app, system WebKit, Rust `portable-pty` for the terminal, Python code reused unchanged | Rust toolchain at build time; two languages |
| B. Electron + Python child | mature terminal (node-pty) and updater | ~150 MB, Node at build and in the bundle, more attack surface |
| C. pywebview + py2app | all Python | non-stdlib deps at runtime, weak terminal/tray support |

## Decision
Tauri 2 window loads the daemon URL; the daemon is plain `python3` from the user's system, run from scripts bundled as app resources. No Node runtime in the bundle; the static UI and a vendored `xterm.js` need no npm build. The Tauri CLI is installed with `cargo install tauri-cli --version '^2'`.

## Consequences
+ small native app; Python logic testable without Rust. − builds need Rust; Linux/Windows later need their own bundling (out of scope).
