#!/usr/bin/env bash
# Copy the agentic kit (CLAUDE.md, .claude/, .SDD/, LICENSES/) into a project.
# Usage: ./install.sh <target-project-dir> [--force]
#   Without --force it refuses to run if any kit file already exists in the target.
#   With --force, conflicting files are first backed up to <target>/.claude-kit-backup-<timestamp>/.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ITEMS=(CLAUDE.md .claude .SDD LICENSES)

usage() { echo "Usage: $0 <target-project-dir> [--force]" >&2; exit 64; }

target=""; force=0
for arg in "$@"; do
  case "$arg" in
    --force) force=1 ;;
    -h|--help) usage ;;
    -*) echo "Unknown option: $arg" >&2; usage ;;
    *) [ -z "$target" ] || usage; target="$arg" ;;
  esac
done
[ -n "$target" ] || usage
[ -d "$target" ] || { echo "Target is not a directory: $target" >&2; exit 1; }
target="$(cd "$target" && pwd)"
[ "$target" != "$KIT" ] || { echo "Target is the kit itself." >&2; exit 1; }

# Files the kit would write, relative to the kit root (never local settings).
files=()
while IFS= read -r f; do files+=("$f"); done < <(
  cd "$KIT" && find "${ITEMS[@]}" -type f ! -name 'settings.local.json' ! -name '.DS_Store' | sort
)

conflicts=()
for f in "${files[@]}"; do
  [ -e "$target/$f" ] && conflicts+=("$f")
done

if [ "${#conflicts[@]}" -gt 0 ]; then
  if [ "$force" -ne 1 ]; then
    echo "Refusing to overwrite ${#conflicts[@]} existing file(s) in $target:" >&2
    printf '  %s\n' "${conflicts[@]}" >&2
    echo "Merge them by hand, or re-run with --force (existing files are backed up first)." >&2
    exit 1
  fi
  backup="$target/.claude-kit-backup-$(date +%Y%m%d-%H%M%S)"
  for f in "${conflicts[@]}"; do
    mkdir -p "$backup/$(dirname "$f")"
    cp -p "$target/$f" "$backup/$f"
  done
  echo "Backed up ${#conflicts[@]} existing file(s) to $backup"
fi

for f in "${files[@]}"; do
  mkdir -p "$target/$(dirname "$f")"
  cp -p "$KIT/$f" "$target/$f"
done
chmod +x "$target/.claude/hooks/guard-bash.sh" "$target/.claude/hooks/guard_bash.py" \
         "$target/.claude/hooks/test_guard_bash.sh"

echo "Installed ${#files[@]} file(s) into $target"
echo "Next steps:"
echo "  1. Fill the placeholders (<your test command>, <your local environment>, ...): grep -rn '<your ' CLAUDE.md .claude .SDD"
echo "  2. Add project rules to the CONFIG block in .claude/hooks/guard_bash.py, then run: bash .claude/hooks/test_guard_bash.sh"
echo "  3. Review .claude/settings.json and commit the kit on a feature branch."
