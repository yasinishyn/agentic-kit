#!/usr/bin/env bash
# agentic-kit bootstrap: fetch the kit from GitHub and install it into a project.
#
#   curl -fsSL https://raw.githubusercontent.com/yasinishyn/agentic-kit/main/get.sh | bash -s -- /path/to/project
#   curl -fsSL https://raw.githubusercontent.com/yasinishyn/agentic-kit/main/get.sh | bash -s -- /path/to/project --update
#
# Everything after the project path is passed to install.py (--only, --all, --yes, --dry-run, --update, ...).
# The kit is cached in ~/.agentic-kit (a read-only copy); each run fetches the latest version from the full repo URL.
# Environment:
#   AGENTIC_KIT_REPO  repository URL        (default https://github.com/yasinishyn/agentic-kit.git)
#   AGENTIC_KIT_REF   branch or tag to use  (default main; pin a tag for reproducible installs)
#   AGENTIC_KIT_HOME  cache folder          (default ~/.agentic-kit)
set -euo pipefail

REPO="${AGENTIC_KIT_REPO:-https://github.com/yasinishyn/agentic-kit.git}"
REF="${AGENTIC_KIT_REF:-main}"
HOME_DIR="${AGENTIC_KIT_HOME:-$HOME/.agentic-kit}"

say() { printf 'agentic-kit: %s\n' "$*" >&2; }
die() { say "$*"; exit 1; }

command -v git >/dev/null 2>&1 || die "git is required"
command -v python3 >/dev/null 2>&1 || die "python3 (3.9+) is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "python3 3.9 or newer is required"

TARGET="${1:-}"
if [ -z "$TARGET" ] || [ "${TARGET#-}" != "$TARGET" ]; then
  TARGET="$PWD"            # no path given: install into the current folder
else
  shift
fi
[ -d "$TARGET" ] || die "project folder not found: $TARGET"

if [ -d "$HOME_DIR/.git" ]; then
  [ -z "$(git -C "$HOME_DIR" status --porcelain)" ] || die "$HOME_DIR has local changes; it is a read-only cache — remove it and re-run"
  say "updating $HOME_DIR from $REPO ($REF)"
  git -C "$HOME_DIR" fetch --quiet --depth 1 "$REPO" "$REF"
  git -C "$HOME_DIR" checkout --quiet --detach FETCH_HEAD
  git -C "$HOME_DIR" remote set-url origin "$REPO"
else
  [ ! -e "$HOME_DIR" ] || die "$HOME_DIR exists but is not a git checkout; move it away or set AGENTIC_KIT_HOME"
  say "downloading $REPO ($REF) to $HOME_DIR"
  git clone --quiet --depth 1 --branch "$REF" "$REPO" "$HOME_DIR"
fi
say "kit version $(git -C "$HOME_DIR" rev-parse --short HEAD)"

# `curl … | bash` makes stdin the script itself: read answers from the terminal instead, when there is one.
if [ -r /dev/tty ] && { : < /dev/tty; } 2>/dev/null; then
  exec python3 "$HOME_DIR/install.py" "$TARGET" "$@" < /dev/tty
else
  exec python3 "$HOME_DIR/install.py" "$TARGET" "$@"
fi
