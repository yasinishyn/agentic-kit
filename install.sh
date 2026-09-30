#!/usr/bin/env bash
# Thin wrapper: all logic is in install.py (additive only; never overwrites your files).
# Usage: ./install.sh /path/to/project [--only sdd,skills,agents,guard,instructions,kanban,kanban-app] [--all] [--yes] [--dry-run] [--update]
#        ./install.sh --only kanban-app [--dry-run] [--from-source]   (Kanban desktop app into ~/Applications; macOS:
#        downloads the checksum-verified release matching this kit, or builds it with cargo tauri under --from-source)
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.py" "$@"
