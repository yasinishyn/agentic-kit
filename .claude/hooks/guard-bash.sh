#!/usr/bin/env bash
# PreToolUse guard for Claude Code Bash calls (agent guardrails).
# Fails CLOSED: any problem running the checker blocks the command (exit 2).
# Policy: agents stage (git add) but never commit/push unless ALLOW_AGENT_COMMITS (then only as the developer),
# and never deploy, publish, call cloud CLIs, touch non-local databases, or read credentials.
# See .claude/hooks/README.md for the rule list, the project CONFIG block and the tests.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "guard-bash: python3 not found; blocking by default (fail-closed)." >&2
  exit 2
fi
python3 "$HERE/guard_bash.py"
rc=$?
if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
  echo "guard-bash: checker error (rc=$rc); blocking by default (fail-closed)." >&2
  exit 2
fi
exit "$rc"
