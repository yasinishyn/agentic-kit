#!/usr/bin/env bash
# package-release.sh — build, sign, verify and package the Kanban app for a GitHub Release (PRD-04, ADR-002).
#
#   package-release.sh [--host-only]
#
# 1. Requires the bundled runtimes in src-tauri/resources/python/{arm64,x86_64} (fetch-python.sh --arch both).
# 2. Signs every Mach-O under them (bin/python3*, *.dylib, lib-dynload/*.so): with the hardened runtime,
#    entitlements.plist, a secure timestamp and "$APPLE_SIGNING_IDENTITY" when SIGNING=1, ad-hoc (-s -) otherwise.
# 3. cargo tauri build --target universal-apple-darwin --bundles app,dmg with the release config
#    (--host-only: host target, app bundle only — for a local check; no DMG).
# 4. Ad-hoc signs the outer bundle when SIGNING is unset; codesign --verify --deep --strict gates both modes.
# 5. Smoke test: each bundled interpreter the host can execute (x86_64 on Apple Silicon only with Rosetta) must
#    `import ssl, sqlite3, ctypes, json`; a failure fails the build.
# 6. Writes Kanban.app.zip (ditto), Kanban.dmg (full build; unsigned: rebuilt with hdiutil from the sealed app) and SHA256SUMS (shasum -a 256, bare file names) to
#    $DIST_DIR (default: <repo>/dist).
#
# SIGNING=1 (set by the release workflow on tag pushes with an imported certificate) needs APPLE_SIGNING_IDENTITY;
# Tauri notarises with APPLE_ID, APPLE_PASSWORD and APPLE_TEAM_ID — a missing one fails the run after signing.
# Without SIGNING=1 every APPLE_* variable is dropped, so a dry run never signs with a real identity.
# KANBAN_PY_RESOURCES overrides the runtime directory for the pre-build check (tests only).
# Exit: 0 ok, 1 failure, 2 usage.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KIT="$(cd "$APP_DIR/../../.." && pwd)"
TAURI_DIR="$APP_DIR/src-tauri"
DEFAULT_PY_RES="$TAURI_DIR/resources/python"
PY_RES="${KANBAN_PY_RESOURCES:-$DEFAULT_PY_RES}"
DIST="${DIST_DIR:-$KIT/dist}"
ENTITLEMENTS="$TAURI_DIR/entitlements.plist"
ARCHES="arm64 x86_64"

usage() { echo "usage: package-release.sh [--host-only]" >&2; }
die() { echo "package-release: $*" >&2; exit 1; }
log() { echo "package-release: $*"; }

host_only=0
while [ $# -gt 0 ]; do
  case "$1" in
    --host-only) host_only=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "package-release: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [ "${SIGNING:-}" != "1" ]; then
  unset APPLE_SIGNING_IDENTITY APPLE_ID APPLE_PASSWORD APPLE_TEAM_ID APPLE_CERTIFICATE APPLE_CERTIFICATE_PASSWORD
  signing=0
else
  signing=1
  [ -n "${APPLE_SIGNING_IDENTITY:-}" ] || die "SIGNING=1 but APPLE_SIGNING_IDENTITY is empty"
fi

for a in $ARCHES; do
  [ -x "$PY_RES/$a/bin/python3" ] \
    || die "bundled runtime missing: $PY_RES/$a/bin/python3 — run plugins/kanban/app/scripts/fetch-python.sh --arch both"
done
# every licence text python.lock lists must be in each runtime (a tree fetched before Q18 has no licences/)
while IFS= read -r rel; do
  for a in $ARCHES; do
    [ -f "$PY_RES/$a/$rel" ] || die "licence file missing from the $a runtime: $rel — run \
plugins/kanban/app/scripts/fetch-python.sh --licences-only --arch both"
  done
done < <(python3 -c 'import json, sys; print("\n".join(json.load(open(sys.argv[1]))["licence_files"]))' \
           "$APP_DIR/python.lock")
[ "$PY_RES" = "$DEFAULT_PY_RES" ] || die "KANBAN_PY_RESOURCES is for the pre-build check only; the build bundles $DEFAULT_PY_RES"
[ -f "$ENTITLEMENTS" ] || die "entitlements missing: $ENTITLEMENTS"
command -v codesign >/dev/null || die "codesign not found (macOS with Xcode command line tools required)"
cargo tauri --version >/dev/null 2>&1 || die "cargo tauri not found: cargo install tauri-cli --locked"

host="$(uname -m)"
[ "$host" = "aarch64" ] && host="arm64"
target_dir="${CARGO_TARGET_DIR:-$TAURI_DIR/target}"
if [ "$host_only" = 1 ]; then
  bundle_dir="$target_dir/release/bundle"
else
  bundle_dir="$target_dir/universal-apple-darwin/release/bundle"
fi
app="$bundle_dir/macos/Kanban.app"

# sign_nested DIR — sign every Mach-O file under DIR (the bundled interpreters, their dylibs and extension modules)
sign_nested() {
  local dir="$1" f n=0
  while IFS= read -r -d '' f; do
    file -b "$f" | grep -q 'Mach-O' || continue
    if [ "$signing" = 1 ]; then
      codesign --force --options runtime --entitlements "$ENTITLEMENTS" --timestamp -s "$APPLE_SIGNING_IDENTITY" "$f"
    else
      codesign --force -s - "$f"
    fi
    n=$((n + 1))
  done < <(find "$dir" -type f \( -path '*/bin/python3*' -o -name '*.dylib' -o -name '*.so' \) -print0)
  [ "$n" -gt 0 ] || die "no Mach-O files found under $dir"
  log "signed $n nested Mach-O file(s) ($([ "$signing" = 1 ] && echo "identity, hardened runtime" || echo ad-hoc))"
}

run_build() {
  local args=(build --config src-tauri/tauri.release.conf.json)
  if [ "$host_only" = 1 ]; then
    args+=(--bundles app)
  else
    args+=(--target universal-apple-darwin --bundles app,dmg)
  fi
  rm -rf "$app" "$bundle_dir/dmg"
  log "cargo tauri ${args[*]}"
  (cd "$APP_DIR" && cargo tauri "${args[@]}")
  [ -d "$app" ] || die "build produced no app bundle at $app"
}

verify() {
  local bundle="$1"
  codesign --verify --deep --strict --verbose=2 "$bundle" || die "codesign verification failed: $bundle"
}

# smoke APP — every bundled interpreter the host can run must import the modules the daemon needs
smoke() {
  local bundle="$1" a exe runner
  for a in $ARCHES; do
    exe="$bundle/Contents/Resources/python/$a/bin/python3"
    [ -x "$exe" ] || die "bundled interpreter missing from the app: $exe"
    runner=()
    if [ "$a" != "$host" ]; then
      if [ "$host" = arm64 ] && [ "$a" = x86_64 ] && /usr/bin/arch -x86_64 /usr/bin/true 2>/dev/null; then
        runner=(/usr/bin/arch -x86_64)
      else
        log "smoke test skipped for $a: this host ($host) cannot execute it (no Rosetta)"
        continue
      fi
    fi
    (cd "${TMPDIR:-/tmp}" && "${runner[@]+"${runner[@]}"}" "$exe" -E -B -c 'import ssl, sqlite3, ctypes, json') \
      || die "smoke test failed: $a interpreter cannot import ssl, sqlite3, ctypes, json (see entitlements.plist)"
    log "smoke test ok: $a"
  done
}

sign_nested "$PY_RES"
run_build
if [ "$signing" = 0 ]; then
  codesign --force -s - "$app"
  log "outer bundle signed ad-hoc (unsigned release)"
fi
verify "$app"
log "codesign --verify --deep --strict ok: $app"
smoke "$app"
if [ "$signing" = 1 ]; then
  for v in APPLE_ID APPLE_PASSWORD APPLE_TEAM_ID; do
    [ -n "${!v:-}" ] || die "notarisation skipped: missing secret $v (the app is signed but not notarised)"
  done
fi

mkdir -p "$DIST"
rm -f "$DIST/Kanban.dmg" "$DIST/Kanban.app.zip" "$DIST/SHA256SUMS"
ditto -c -k --keepParent "$app" "$DIST/Kanban.app.zip"
files=()
if [ "$host_only" = 0 ]; then
  if [ "$signing" = 1 ]; then
    dmgs=("$bundle_dir"/dmg/*.dmg)
    [ "${#dmgs[@]}" -eq 1 ] && [ -f "${dmgs[0]}" ] || die "expected exactly one DMG in $bundle_dir/dmg"
    cp "${dmgs[0]}" "$DIST/Kanban.dmg"
  else
    # Tauri built its DMG before the ad-hoc seal above: rebuild it from the sealed, verified app
    dmg_src="$(mktemp -d "${TMPDIR:-/tmp}/kanban-dmg.XXXXXX")"
    ditto "$app" "$dmg_src/Kanban.app"
    ln -s /Applications "$dmg_src/Applications"
    hdiutil create -quiet -volname Kanban -srcfolder "$dmg_src" -ov -format UDZO "$DIST/Kanban.dmg" \
      || { rm -rf "$dmg_src"; die "hdiutil could not build Kanban.dmg"; }
    rm -rf "$dmg_src"
    log "Kanban.dmg rebuilt from the sealed app (unsigned release)"
  fi
  files+=(Kanban.dmg)
else
  log "--host-only: no DMG"
fi
files+=(Kanban.app.zip)
(cd "$DIST" && shasum -a 256 "${files[@]}" > SHA256SUMS)
log "wrote $DIST: ${files[*]} SHA256SUMS"
cat "$DIST/SHA256SUMS"
