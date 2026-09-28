#!/usr/bin/env python3
"""Install parts of the agentic kit into a project. Additive only: never overwrites your files.

  python3 install.py /path/to/project                     # interactive: pick components
  python3 install.py /path/to/project --only sdd,agents   # non-interactive selection
  python3 install.py /path/to/project --all --yes         # everything, no questions
  python3 install.py /path/to/project --dry-run           # show what would happen
  python3 install.py /path/to/project --update            # after `git pull`: refresh kit files you have not edited
  python3 install.py --only kanban-app [--dry-run]        # build the Kanban desktop app (macOS) into ~/Applications

Rules:
- an existing file is kept, never overwritten;
- a skill or agent whose name already exists is skipped as a whole;
- .claude/settings.json is merged by adding missing entries only, after a backup;
- CLAUDE.md is created only if missing (otherwise the kit text goes to .claude/agentic-kit/CLAUDE.kit.md).
Installed files and their hashes are recorded in .claude/agentic-kit/installed.json, which is what makes
--update safe: only files still identical to what the kit installed are refreshed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent
MANIFEST = Path(".claude/agentic-kit/installed.json")

COMPONENTS = {
    "sdd": "SDD flow: the `sdd` skill + .SDD/ (process README, spec templates, specs/)",
    "skills": "Engineering skills: test-driven-development, systematic-debugging, verification-before-completion",
    "agents": "Review agents: architect, qa-verifier",
    "guard": "Guardrails: guard hook (blocks agent commit/push by default, deploy/aws/credential reads) + deny rules merged into .claude/settings.json",
    "instructions": "CLAUDE.md template + .claude/memory/ (long-term memory index)",
    "kanban": "Kanban plugin: board over .SDD/specs (tickets = spec folders, columns = ADLC stages) + MCP tools + `ticket` skill (claude CLI)",
    "kanban-app": "Kanban desktop app (macOS): builds Kanban.app from source with cargo tauri and copies it to "
                  "~/Applications. Only with --only kanban-app (never by default, --all or --update)",
}
DEFAULT_ON = ["sdd", "skills", "agents", "guard", "instructions"]
EXPLICIT_ONLY = {"kanban-app"}  # never selected by --all, the interactive prompt, the manifest or --update
NOT_FILES = {"kanban", "kanban-app"}  # components that copy no kit files into the project
# Merged into the project's .claude/settings.json by `guard` and `kanban` (architecture §3.3, §8): approving a spec
# from chat always asks the human, and the Read tool cannot read the board's UI token (the Bash guard covers Bash).
KANBAN_PERMISSIONS = {
    "ask": ["mcp__plugin_kanban_kanban__kanban_approve"],
    "deny": ["Read(~/Library/Application Support/Kanban/ui.token)"],
}
APP_DIR = KIT / "plugins/kanban/app"
APP_BUNDLE = APP_DIR / "src-tauri/target/release/bundle/macos/Kanban.app"
APP_HINTS = {  # printed for the user to run; the installer itself never downloads or runs remote scripts
    "cargo": "Rust (cargo): curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   then: source ~/.cargo/env",
    "tauri": "Tauri CLI: cargo install tauri-cli --version '^2' --locked",
    "xcode": "Xcode command line tools: xcode-select --install",
}
SKILLS = {"sdd": ["sdd"], "skills": ["test-driven-development", "systematic-debugging", "verification-before-completion"]}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def kit_version() -> str:
    try:
        return subprocess.run(["git", "-C", str(KIT), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


class Installer:
    def __init__(self, target: Path, dry: bool, update: bool, yes: bool):
        self.target, self.dry, self.update, self.yes = target, dry, update, yes
        path = target / MANIFEST
        self.manifest = json.loads(path.read_text()) if path.exists() else {"files": {}}
        self.report: list[tuple[str, str]] = []  # (action, path)
        self.app_ok = True  # False after a failed kanban-app build (non-zero exit)

    # ---------------------------------------------------------------- file primitives
    def put(self, src: Path, rel: str) -> None:
        dst = self.target / rel
        if dst.exists():
            recorded = self.manifest["files"].get(rel)
            if dst.is_file() and sha(dst) == sha(src):
                self.report.append(("same", rel))
                self.manifest["files"][rel] = sha(src)
            elif self.update and recorded and dst.is_file() and sha(dst) == recorded:
                self._copy(src, dst, rel, "updated")
            else:
                self.report.append(("kept (yours)", rel))
            return
        self._copy(src, dst, rel, "added")

    def _copy(self, src: Path, dst: Path, rel: str, action: str) -> None:
        self.report.append((action, rel))
        if self.dry:
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        self.manifest["files"][rel] = sha(src)

    def put_tree(self, src_dir: Path, rel_dir: str) -> None:
        for src in sorted(p for p in src_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                          and p.name != ".DS_Store"):
            self.put(src, f"{rel_dir}/{src.relative_to(src_dir).as_posix()}")

    def owned_by_kit(self, rel_prefix: str) -> bool:
        return any(k.startswith(rel_prefix) for k in self.manifest["files"])

    # ---------------------------------------------------------------- components
    def skill(self, name: str) -> None:
        rel = f".claude/skills/{name}"
        if (self.target / rel).exists() and not self.owned_by_kit(rel + "/"):
            self.report.append(("skipped: you already have this skill", rel))
            return
        self.put_tree(KIT / rel, rel)

    def sdd(self) -> None:
        self.skill("sdd")
        sdd_dir = self.target / ".SDD"
        if sdd_dir.exists() and not self.owned_by_kit(".SDD/"):
            # an existing SDD setup: add only missing templates, never touch its README or specs
            self.report.append(("existing .SDD/ found: only missing templates are added", ".SDD/"))
            self.put_tree(KIT / ".SDD/templates", ".SDD/templates")
            return
        self.put_tree(KIT / ".SDD", ".SDD")

    def skills(self) -> None:
        for name in SKILLS["skills"]:
            self.skill(name)

    def agents(self) -> None:
        for src in sorted((KIT / ".claude/agents").glob("*.md")):
            rel = f".claude/agents/{src.name}"
            if (self.target / rel).exists() and rel not in self.manifest["files"]:
                self.report.append(("skipped: you already have this agent", rel))
                continue
            self.put(src, rel)

    def existing_bash_guard(self) -> str | None:
        """A PreToolUse Bash hook that is not ours means the project already has a guard."""
        for name in ("settings.json", "settings.local.json"):
            path = self.target / ".claude" / name
            try:
                cur = json.loads(path.read_text()) if path.exists() else {}
            except ValueError:
                continue
            for group in cur.get("hooks", {}).get("PreToolUse", []):
                if group.get("matcher", "") in ("Bash", "*", ""):
                    for hook in group.get("hooks", []):
                        if ".claude/hooks/guard-bash.sh" not in hook.get("command", ""):
                            return f"{name}: {hook.get('command', '')}"
        return None

    def guard(self) -> None:
        rel = ".claude/hooks"
        other = self.existing_bash_guard()
        if other and not self.owned_by_kit(rel + "/"):
            self.report.append((f"skipped: the project already has a Bash guard hook ({other})", rel))
            return
        clash = [p.name for p in (KIT / rel).iterdir() if (self.target / rel / p.name).exists()
                 and f"{rel}/{p.name}" not in self.manifest["files"]]
        if clash:
            self.report.append((f"skipped: .claude/hooks already has {', '.join(sorted(clash))}", rel))
            return
        self.put_tree(KIT / rel, rel)
        if not self.dry:
            for script in ("guard-bash.sh", "guard_bash.py", "test_guard_bash.sh"):
                path = self.target / rel / script
                if path.exists():
                    path.chmod(path.stat().st_mode | 0o111)
        kit = json.loads((KIT / ".claude/settings.json").read_text())
        self.merge_settings(add_permissions(kit, KANBAN_PERMISSIONS))

    def merge_settings(self, kit: dict) -> None:
        """Add missing permission rules and the guard hook (when `kit` has one); never remove or change values."""
        rel = ".claude/settings.json"
        path = self.target / rel
        if not path.exists():
            self.report.append(("added", rel))
            if not self.dry:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(kit, indent=2) + "\n")
            return
        try:
            cur = json.loads(path.read_text())
        except ValueError:
            self.report.append(("skipped: not valid JSON, merge the permission rules and guard hook by hand", rel))
            return
        before = json.dumps(cur, sort_keys=True)
        add_permissions(cur, kit.get("permissions", {}))
        for kit_hook in kit.get("hooks", {}).get("PreToolUse", [])[:1]:
            pre = cur.setdefault("hooks", {}).setdefault("PreToolUse", [])
            commands = {h.get("command") for group in pre for h in group.get("hooks", [])}
            if kit_hook["hooks"][0]["command"] not in commands:
                pre.append(kit_hook)
        if json.dumps(cur, sort_keys=True) == before:
            self.report.append(("same", rel))
            return
        self.report.append(("merged (added entries only; backup saved)", rel))
        if not self.dry:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, path.with_name(f"settings.json.bak-{stamp}"))
            path.write_text(json.dumps(cur, indent=2) + "\n")

    def instructions(self) -> None:
        if (self.target / "CLAUDE.md").exists() and "CLAUDE.md" not in self.manifest["files"]:
            # imports resolve relative to the importing file, so fix the memory import for the new location
            text = (KIT / "CLAUDE.md").read_text().replace("@.claude/memory/MEMORY.md", "@../memory/MEMORY.md")
            staged = Path(tempfile.mkdtemp()) / "CLAUDE.kit.md"
            staged.write_text(text)
            self.put(staged, ".claude/agentic-kit/CLAUDE.kit.md")
            self.report.append(("CLAUDE.md exists: kit text saved beside it; add `@.claude/agentic-kit/CLAUDE.kit.md`"
                                " to your CLAUDE.md if you want it", "CLAUDE.md"))
        else:
            self.put(KIT / "CLAUDE.md", "CLAUDE.md")
        self.put_tree(KIT / ".claude/memory", ".claude/memory")

    def kanban(self, scope: str) -> None:
        self.merge_settings({"permissions": KANBAN_PERMISSIONS})
        source = marketplace_source()
        cmds = [["claude", "plugin", "marketplace", "add", source, "--scope", scope],
                ["claude", "plugin", "install", "kanban@agentic-kit", "--scope", scope]]
        printable = " && ".join(" ".join(c) for c in cmds)
        if self.dry or not shutil.which("claude"):
            self.report.append(("run: " + printable, "kanban plugin"))
            return
        for cmd in cmds:
            res = subprocess.run(cmd, cwd=self.target, capture_output=True, text=True)
            out = (res.stdout + res.stderr).strip().splitlines()
            self.report.append((("ok: " if res.returncode == 0 else f"FAILED ({res.returncode}): ")
                                + " ".join(cmd[1:4]), out[-1] if out else ""))

    def kanban_app(self) -> None:
        """Explicit-only component (--only kanban-app): builds and copies the app; nothing goes into the project."""
        self.app_ok = build_kanban_app(self.dry)
        self.report.append((("run: shown above (dry run)" if self.dry else "ok: built and copied to ~/Applications")
                            if self.app_ok else "FAILED: see the Kanban desktop app notes above",
                            "kanban-app"))

    def licences(self) -> None:
        self.put_tree(KIT / "LICENSES", ".claude/agentic-kit/LICENSES")

    def save_manifest(self, components: list[str]) -> None:
        if self.dry:
            return
        self.manifest.update({"kit": str(KIT), "kit_version": kit_version(),
                              "installed_at": dt.datetime.now().isoformat(timespec="seconds"),
                              "components": sorted((set(self.manifest.get("components", [])) | set(components))
                                                   - EXPLICIT_ONLY)})
        path = self.target / MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.manifest, indent=1, sort_keys=True) + "\n")


def add_permissions(settings: dict, rules: dict) -> dict:
    """Append the missing `deny`/`ask` rules to settings["permissions"] (in place); returns settings."""
    perms = settings.setdefault("permissions", {})
    for key in ("deny", "ask"):
        if rules.get(key):
            have = perms.setdefault(key, [])
            have += [r for r in rules[key] if r not in have]
    return settings


def find_tool(name: str) -> str | None:
    """`shutil.which`, also looking in ~/.cargo/bin (what `source ~/.cargo/env` would add to PATH)."""
    return shutil.which(name) or shutil.which(name, path=str(Path.home() / ".cargo/bin"))


def cargo_env(cargo: str) -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(Path(cargo).parent), env.get("PATH", "")])
    return env


def app_prereqs() -> tuple[str | None, list[str]]:
    """(cargo path or None, the list of missing prerequisites: keys of APP_HINTS)."""
    missing = []
    cargo = find_tool("cargo")
    if not cargo:
        missing += ["cargo", "tauri"]
    else:
        try:
            ok = subprocess.run([cargo, "tauri", "--version"], capture_output=True, text=True, timeout=60,
                                env=cargo_env(cargo)).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            missing.append("tauri")
    xcode = shutil.which("xcode-select")
    try:
        has_clt = bool(xcode) and subprocess.run([xcode, "-p"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        has_clt = False
    if not has_clt:
        missing.append("xcode")
    return cargo, missing


def build_kanban_app(dry: bool) -> bool:
    """Build Kanban.app from the kit's source and copy it to ~/Applications (replacing an existing copy).

    Returns False when a prerequisite is missing or a step fails. Never downloads or runs remote scripts: missing
    tools are reported with the commands for the user to run."""
    dest = Path.home() / "Applications" / "Kanban.app"
    build = ["cargo", "tauri", "build", "--bundles", "app"]
    print("Kanban desktop app:")
    print(f"  1. (cd {APP_DIR} && {' '.join(build)})")
    print(f"  2. copy {APP_BUNDLE} -> {dest} (replaces an existing copy)")
    cargo, missing = app_prereqs()
    if sys.platform != "darwin":
        print("  The Kanban app is macOS only.")
        return dry
    if missing:
        print(("  Note (dry run): missing" if dry else "  Missing") + " prerequisites; install them, then re-run:")
        for key in missing:
            print(f"    - {APP_HINTS[key]}")
        return dry
    if dry:
        print("  Dry run: nothing built or copied.")
        return True
    print("  Building (a first build takes a few minutes) …", flush=True)
    res = subprocess.run([cargo, *build[1:]], cwd=APP_DIR, env=cargo_env(cargo))
    if res.returncode != 0 or not APP_BUNDLE.is_dir():
        print(f"  FAILED: cargo tauri build exited {res.returncode}; see the output above.")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(APP_BUNDLE, dest, symlinks=True)
    print(f"  Installed {dest}. Open it from ~/Applications (it starts the board daemon if needed).")
    return True


def marketplace_source() -> str:
    """GitHub owner/repo when the kit is a clone of a GitHub repo (shareable), else the local path."""
    try:
        url = subprocess.run(["git", "-C", str(KIT), "remote", "get-url", "origin"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except Exception:
        return str(KIT)
    for prefix in ("https://github.com/", "git@github.com:"):
        if url.startswith(prefix):
            return url[len(prefix):].removesuffix(".git")
    return url or str(KIT)


def ask_components() -> list[str]:
    print("Which parts do you want? (Enter = the default shown in brackets)\n")
    chosen = []
    for key, text in COMPONENTS.items():
        if key in EXPLICIT_ONLY:
            continue
        default = key in DEFAULT_ON
        reply = input(f"  {key:<12} {text}\n  install? [{'Y/n' if default else 'y/N'}] ").strip().lower()
        if (reply in ("y", "yes")) or (reply == "" and default):
            chosen.append(key)
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="project folder to install into")
    ap.add_argument("--only", help="comma-separated components: " + ",".join(COMPONENTS))
    ap.add_argument("--all", action="store_true", help="all components")
    ap.add_argument("--yes", "-y", action="store_true", help="no questions (default components unless --only/--all)")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("--update", action="store_true", help="refresh kit files you have not edited (after git pull)")
    ap.add_argument("--kanban-scope", choices=["user", "project", "local"], default="user",
                    help="where the kanban plugin is enabled (default: user)")
    ap.add_argument("--list", action="store_true", help="list the components and exit")
    args = ap.parse_args()

    if args.list:
        for key, text in COMPONENTS.items():
            print(f"{key:<12} {text}")
        return 0
    only = [c.strip() for c in (args.only or "").split(",") if c.strip()]
    unknown = [c for c in only if c not in COMPONENTS]
    if unknown:
        ap.error(f"unknown component(s): {', '.join(unknown)}")
    if only and set(only) <= EXPLICIT_ONLY and not args.target:  # the app needs no project folder
        return 0 if build_kanban_app(args.dry_run) else 1
    if not args.target:
        ap.error("target project folder is required")
    target = Path(args.target).expanduser().resolve()
    if not target.is_dir():
        ap.error(f"not a folder: {target}")
    if target == KIT:
        ap.error("the target is the kit itself")

    manifest_path = target / MANIFEST
    if only:
        components = only
    elif args.all:
        components = [c for c in COMPONENTS if c not in EXPLICIT_ONLY]
    elif args.update and manifest_path.exists():
        components = [c for c in json.loads(manifest_path.read_text()).get("components", DEFAULT_ON)
                      if c not in EXPLICIT_ONLY]
    elif args.yes or not sys.stdin.isatty():
        components = DEFAULT_ON
    else:
        components = ask_components()
    if not components:
        print("Nothing selected.")
        return 0

    inst = Installer(target, args.dry_run, args.update, args.yes)
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Installing into {target}: {', '.join(components)}\n")
    for comp in COMPONENTS:  # fixed order
        if comp not in components:
            continue
        if comp == "kanban":
            inst.kanban(args.kanban_scope)
        else:
            getattr(inst, comp.replace("-", "_"))()
    if any(c not in NOT_FILES for c in components):
        inst.licences()
    inst.save_manifest(components)

    width = min(max((len(r) for a, r in inst.report if a != "same"), default=0), 56)
    for action, rel in inst.report:
        if action != "same":
            print(f"  {rel:<{width}}  {action}")
    kinds = ("added", "updated", "merged", "kept", "skipped", "same", "run", "ok", "FAILED")
    counts = {k: sum(a.startswith(k) for a, _ in inst.report) for k in kinds}
    print("\nSummary: " + ", ".join(f"{v} {k}" for k, v in counts.items() if v)
          + ". Nothing of yours was overwritten.")
    if not args.dry_run:
        print("\nNext steps:")
        if "guard" in components:
            print("  - Add project rules to the CONFIG block in .claude/hooks/guard_bash.py, then run:"
                  " bash .claude/hooks/test_guard_bash.sh")
        print("  - Fill the placeholders: grep -rn '<your ' CLAUDE.md .claude .SDD")
        print("  - Start Claude Code in the project root (restart it if it was running).")
    return 0 if inst.app_ok else 1


if __name__ == "__main__":
    sys.exit(main())
