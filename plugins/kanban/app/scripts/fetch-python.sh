#!/usr/bin/env bash
# fetch-python.sh — fetch the pinned python-build-standalone runtime(s) for the Kanban app (PRD-01, ADR-001).
#
#   fetch-python.sh [--arch arm64|x86_64|both] [--lock <file>] [--dest <dir>] [--licences-only]
#
# For each arch in app/python.lock: download over HTTPS, verify the sha256 BEFORE unpacking, unpack into a temp dir,
# prune (tests, idlelib, tkinter, turtledemo, ensurepip, lib2to3, pydoc_data, Tcl/Tk, include/, config-*, static
# libpython, shipped __pycache__), copy the component licence texts pinned in app/licences/python-runtime/ into the
# tree as licences/ (Q18), check every licence file in the lock is there, precompile the stdlib with
# `compileall --invalidation-mode unchecked-hash` (host-arch bundled interpreter for both trees), enforce the unpacked
# size ceiling, and only then move the tree to <dest>/<arch> (default: src-tauri/resources/python/<arch>), replacing
# an older one. Any failure leaves <dest> untouched. `file://` URLs are accepted only with --lock (tests).
# --licences-only: download nothing; replace <dest>/<arch>/licences/ of an already fetched tree and re-run the licence
# check (a changed licence pin without a changed runtime).
# Exit: 0 ok, 1 failure, 2 usage.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

usage() { echo "usage: fetch-python.sh [--arch arm64|x86_64|both] [--lock <file>] [--dest <dir>] [--licences-only]" >&2; }
die() { echo "fetch-python: $*" >&2; exit 1; }

arch="both"
lock="$APP_DIR/python.lock"
custom_lock=0
dest="$APP_DIR/src-tauri/resources/python"
licence_src="$APP_DIR/licences/python-runtime"
licences_only=0
while [ $# -gt 0 ]; do
  case "$1" in
    --arch) [ $# -ge 2 ] || { usage; exit 2; }; arch="$2"; shift 2 ;;
    --lock) [ $# -ge 2 ] || { usage; exit 2; }; lock="$2"; custom_lock=1; shift 2 ;;
    --dest) [ $# -ge 2 ] || { usage; exit 2; }; dest="$2"; shift 2 ;;
    --licences-only) licences_only=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "fetch-python: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

host="$(uname -m)"
[ "$host" = "aarch64" ] && host="arm64"
case "$arch" in
  arm64|x86_64) arches="$arch" ;;
  both) if [ "$host" = "x86_64" ]; then arches="x86_64 arm64"; else arches="arm64 x86_64"; fi ;;
  *) echo "fetch-python: unknown arch: $arch" >&2; usage; exit 2 ;;
esac

[ -f "$lock" ] || die "lock file not found: $lock"

# lock_get KEY... → the value (a list prints one item per line)
lock_get() {
  python3 - "$lock" "$@" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
for key in sys.argv[2:]:
    value = value[key]
print("\n".join(value) if isinstance(value, list) else value)
PY
}

version="$(lock_get version)" || die "lock: missing version"
max_mb="$(lock_get max_unpacked_mb)" || die "lock: missing max_unpacked_mb"
licences="$(lock_get licence_files)" || die "lock: missing licence_files"
case "$max_mb" in ''|*[!0-9]*) die "lock: max_unpacked_mb must be a whole number, got '$max_mb'" ;; esac
[ -n "$licences" ] || die "lock: licence_files is empty"
mm="${version%.*}"                      # 3.12.14 → 3.12
stdlib_rel="lib/python$mm"
[ -d "$licence_src" ] || die "component licence folder not found: $licence_src"

# add_licences TREE — put the pinned component licence texts into TREE/licences (TREE has none yet)
add_licences() {
  [ ! -e "$1/licences" ] || die "unexpected licences/ already in $1"
  cp -R "$licence_src" "$1/licences"
}

# check_licences TREE LABEL — every licence file listed in the lock exists in TREE
check_licences() {
  while IFS= read -r rel; do
    [ -f "$1/$rel" ] || die "$2: licence file missing after prune: $rel"
  done <<EOF
$licences
EOF
}

if [ "$licences_only" = 1 ]; then
  for a in $arches; do
    [ -x "$dest/$a/bin/python3" ] || die "$a: no runtime in $dest/$a (run a full fetch first)"
  done
  # stage and check every arch outside <dest> (anything left inside a tree is bundled into the app), then swap
  stage="$(mktemp -d "${TMPDIR:-/tmp}/fetch-python-licences.XXXXXX")"
  trap 'rm -rf "$stage"' EXIT
  for a in $arches; do
    mkdir "$stage/$a"
    cp -R "$licence_src" "$stage/$a/licences" || die "$a: cannot copy $licence_src"
    # the check sees the tree as it will be: its own files plus the new licences/
    while IFS= read -r rel; do
      case "$rel" in
        licences/*) [ -f "$stage/$a/$rel" ] || die "$a: licence file missing: $rel" ;;
        *) [ -f "$dest/$a/$rel" ] || die "$a: licence file missing: $rel" ;;
      esac
    done <<EOF
$licences
EOF
  done
  for a in $arches; do
    tree="${dest:?}/${a:?}"
    [ ! -e "$tree/licences" ] || mv "$tree/licences" "$stage/$a/licences.old"
    mv "$stage/$a/licences" "$tree/licences"
    echo "fetch-python: $a: licences refreshed in $tree/licences"
  done
  exit 0
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/fetch-python.XXXXXX")"
cleanup() {
  rm -rf "$work"
  for a in arm64 x86_64; do rm -rf "$dest/.$a.new.$$" "$dest/.$a.old.$$"; done
}
trap cleanup EXIT

download() { # url out
  local url="$1" out="$2"
  case "$url" in
    https://github.com/astral-sh/python-build-standalone/*)
      curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --location --silent --show-error \
        --retry 2 -o "$out.part" "$url" || { rm -f "$out.part"; die "download failed: $url"; } ;;
    file://*)
      [ "$custom_lock" = 1 ] || die "refusing $url: file:// URLs are accepted only with --lock"
      curl --proto '=file' --fail --silent --show-error -o "$out.part" "$url" \
        || { rm -f "$out.part"; die "download failed: $url"; } ;;
    *) die "refusing $url: only https://github.com/astral-sh/python-build-standalone/ URLs are allowed" ;;
  esac
  mv "$out.part" "$out"
}

prune() { # tree
  local tree="$1" std="$1/$stdlib_rel"
  rm -rf "$std/test" "$std/idlelib" "$std/tkinter" "$std/turtledemo" "$std/ensurepip" "$std/lib2to3" \
         "$std/pydoc_data" "$tree/lib/tcl8.6" "$tree/lib/tk8.6" "$tree/include"
  rm -rf "$std"/config-*
  find "$std" -depth -type d \( -name test -o -name tests \) -exec rm -rf {} +
  find "$tree" -depth -type d -name __pycache__ -exec rm -rf {} +
  find "$tree" \( -name 'libtcl*' -o -name 'libtk*' -o -name 'libpython*.a' -o -name '_tkinter*.so' \) \
    -exec rm -rf {} +
  # measured on the real 20260924 archive (2026-09-29): Tcl/Tk 9.0 trees, pip, man pages, pkgconfig, dev launchers
  # Tcl packages beside them (itcl, thread; measured 2026-09-30) are useless without libtcl and unlicensed here (Q18)
  rm -rf "$tree"/lib/tcl[0-9]* "$tree"/lib/tk[0-9]* "$tree"/lib/itcl[0-9]* "$tree"/lib/thread[0-9]* \
         "$tree/lib/pkgconfig" "$tree/share" \
         "$std"/site-packages/pip "$std"/site-packages/pip-*.dist-info
  # the stripped build's interpreter is statically linked: its libpython dylib is dead weight (18 MB) unless the
  # interpreter actually links it, which otool would show
  local real
  real="$(cd "$tree/bin" && readlink python3 || echo python3)"
  if ! otool -L "$tree/bin/$real" 2>/dev/null | grep -q 'libpython'; then
    rm -f "$tree"/lib/libpython*.dylib
  fi
  # one real bin/python3: Tauri copies symlinks as full files, which would triple the binary
  if [ "$real" != python3 ]; then
    rm -f "$tree/bin/python3"
    mv "$tree/bin/$real" "$tree/bin/python3"
  fi
  find "$tree/bin" -mindepth 1 ! -name python3 -exec rm -rf {} +
}

# 1. download, verify, unpack, prune, licence check — per arch, all in $work
for a in $arches; do
  url="$(lock_get arches "$a" url)" || die "lock: no url for $a"
  sum="$(lock_get arches "$a" sha256)" || die "lock: no sha256 for $a"
  archive="$work/$a.tar.gz"
  download "$url" "$archive"
  actual="$(shasum -a 256 "$archive" | awk '{print $1}')"
  [ "$actual" = "$sum" ] || die "$a: sha256 mismatch (expected $sum, got $actual); nothing unpacked"
  mkdir "$work/$a"
  tar -xzf "$archive" -C "$work/$a" || die "$a: cannot unpack $archive"
  rm -f "$archive"
  tree="$work/$a/python"
  [ -x "$tree/bin/python3" ] && [ -d "$tree/$stdlib_rel" ] \
    || die "$a: unexpected archive layout (no python/bin/python3 or python/$stdlib_rel)"
  prune "$tree"
  add_licences "$tree"
  check_licences "$tree" "$a"
done

# 2. precompile every tree with the host-arch interpreter (bytecode is arch-independent)
if [ -x "$work/$host/python/bin/python3" ]; then
  compiler="$work/$host/python/bin/python3"
elif [ -x "$dest/$host/bin/python3" ]; then
  compiler="$dest/$host/bin/python3"
else
  set -- $arches
  compiler="$work/$1/python/bin/python3"   # the tree's own interpreter (Rosetta when it is x86_64 on arm64)
fi
reported="$("$compiler" --version 2>&1)" || die "cannot run $compiler --version"
[ "$reported" = "Python $version" ] || die "$compiler reports '$reported', the lock pins Python $version"
for a in $arches; do
  "$compiler" -E -s -m compileall -q -j0 --invalidation-mode unchecked-hash "$work/$a/python/$stdlib_rel" \
    || die "$a: compileall failed"
done

# 3. size ceiling (measured after pruning and compiling)
for a in $arches; do
  kb="$(du -sk "$work/$a/python" | awk '{print $1}')"
  mb="$(awk -v kb="$kb" 'BEGIN { printf "%.1f", kb / 1024 }')"
  if [ "$kb" -gt $((max_mb * 1024)) ]; then
    die "$a: unpacked size $mb MB exceeds the ceiling $max_mb MB (raise it only with an ADR-001 note)"
  fi
  echo "fetch-python: $a: unpacked size $mb MB (ceiling $max_mb MB)"
done

# 4. install: stage next to the destination, then swap (an older tree goes only after every check passed)
mkdir -p "$dest"
for a in $arches; do
  mv "$work/$a/python" "$dest/.$a.new.$$"
  [ -e "$dest/$a" ] && mv "$dest/$a" "$dest/.$a.old.$$"
  mv "$dest/.$a.new.$$" "$dest/$a"
  rm -rf "$dest/.$a.old.$$"
  echo "fetch-python: $a: Python $version installed in $dest/$a"
done
