#!/usr/bin/env bash
# Thin wrapper: all logic is in install.py (additive only; never overwrites your files).
# Usage: ./install.sh /path/to/project [--only sdd,skills,agents,guard,instructions,kanban] [--all] [--yes] [--dry-run] [--update]
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.py" "$@"
