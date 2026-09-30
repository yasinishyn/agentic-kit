#!/usr/bin/env python3
"""Install parts of the agentic kit into a project. Additive only: never overwrites your files.

  python3 install.py /path/to/project                     # interactive: pick components
  python3 install.py /path/to/project --only sdd,agents   # non-interactive selection
  python3 install.py /path/to/project --all --yes         # everything, no questions
  python3 install.py /path/to/project --dry-run           # show what would happen
  python3 install.py /path/to/project --update            # after `git pull`: refresh kit files you have not edited
  python3 install.py --only kanban-app [--dry-run]        # download the Kanban desktop app (macOS) into ~/Applications
  python3 install.py --only kanban-app --from-source      # build it with cargo tauri instead

Rules:
- an existing file is kept, never overwritten;
- a skill or agent whose name already exists is skipped as a whole;
- .claude/settings.json is merged by adding missing entries only, after a backup;
- CLAUDE.md is created only if missing (otherwise the kit text goes to .claude/agentic-kit/CLAUDE.kit.md).
Installed files and their hashes are recorded in .claude/agentic-kit/installed.json, which is what makes
--update safe: only files still identical to what the kit installed are refreshed.

Kanban desktop app (macOS, ADR-003): downloads Kanban.app.zip of the release `kanban-v<plugin version>` (fallback: the
newest published, non-prerelease kanban-v* release) from https://github.com/<owner>/<repo>/releases, where the
repository is AGENTIC_KIT_REPO, else the kit clone's origin remote, else github.com/yasinishyn/agentic-kit. Every
request and redirect must be HTTPS to github.com, api.github.com, objects.githubusercontent.com or
release-assets.githubusercontent.com (at most 5 redirects). The zip is verified against plugins/kanban/app/releases.lock
when it pins the tag, else against the release's SHA256SUMS (integrity only), then unpacked, checked and swapped in.
AGENTIC_KIT_RELEASES_URL (tests only) replaces the releases base when it is a loopback URL; the release listing is then
read from <that URL>/api/releases?per_page=100.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
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
    "kanban-app": "Kanban desktop app (download): the checksum-verified Kanban.app release matching this kit, into "
                  "~/Applications (macOS only; follows kanban in the menu and under --yes, included by --all; "
                  "--from-source builds it with cargo tauri instead)",
}
DEFAULT_ON = ["sdd", "skills", "agents", "guard", "instructions"]
MACOS_ONLY = {"kanban-app"}  # skipped with a note elsewhere
NO_TARGET = {"kanban-app"}  # may run without a project folder
NOT_FILES = {"kanban", "kanban-app"}  # components that copy no kit files into the project
# Merged into the project's .claude/settings.json by `guard` and `kanban` (architecture §3.3, §8): approving a spec
# from chat always asks the human, and the Read tool cannot read the board's UI token (the Bash guard covers Bash).
KANBAN_PERMISSIONS = {
    "ask": ["mcp__plugin_kanban_kanban__kanban_approve"],
    "deny": ["Read(~/Library/Application Support/Kanban/ui.token)"],
}
APP_DIR = KIT / "plugins/kanban/app"
APP_BUNDLE = APP_DIR / "src-tauri/target/release/bundle/macos/Kanban.app"
APP_LOCK = APP_DIR / "releases.lock"
APP_LOCK_REL = "plugins/kanban/app/releases.lock"
APP_CONF = APP_DIR / "src-tauri/tauri.conf.json"  # read only: `identifier` is the expected bundle id
PLUGIN_JSON = KIT / "plugins/kanban/.claude-plugin/plugin.json"
APP_ZIP = "Kanban.app.zip"
DEFAULT_REPO = "https://github.com/yasinishyn/agentic-kit"
ALLOWED_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
MAX_HOPS = 5
HTTP_TIMEOUT = 30
USER_AGENT = "agentic-kit-installer"
# --from-source prerequisites, printed for the user to run; the installer never runs remote scripts (the only thing it
# downloads is the Kanban.app release, verified as described in the module docstring)
APP_HINTS = {
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
    def __init__(self, target: Path, dry: bool, update: bool, yes: bool, from_source: bool = False):
        self.target, self.dry, self.update, self.yes, self.from_source = target, dry, update, yes, from_source
        path = target / MANIFEST
        self.manifest = json.loads(path.read_text()) if path.exists() else {"files": {}}
        self.report: list[tuple[str, str]] = []  # (action, path)
        self.app_ok = True  # False after a failed or skipped kanban-app install (non-zero exit)

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
        """Downloads (or with --from-source builds) Kanban.app into ~/Applications; nothing goes into the project.
        The manifest records the component and the installed version only after success."""
        if self.from_source:
            self.app_ok, record = build_kanban_app(self.dry), None
            if self.app_ok and not self.dry:
                version = installed_app_version(app_dest())
                record = {"version": version, "tag": None, "source": "build"}
        else:
            self.app_ok, record = download_kanban_app(self.dry, self.update, self.yes)
        if record:
            self.manifest["kanban_app"] = record
        self.report.append((("run: shown above (dry run)" if self.dry else "ok: see the Kanban desktop app notes above")
                            if self.app_ok else "FAILED: see the Kanban desktop app notes above",
                            "kanban-app"))

    def licences(self) -> None:
        self.put_tree(KIT / "LICENSES", ".claude/agentic-kit/LICENSES")

    def save_manifest(self, components: list[str]) -> None:
        if self.dry:
            return
        new = set(components)
        if not self.app_ok:  # recorded only after success (an earlier successful install stays recorded)
            new.discard("kanban-app")
        self.manifest.update({"kit": str(KIT), "kit_version": kit_version(),
                              "installed_at": dt.datetime.now().isoformat(timespec="seconds"),
                              "components": sorted(set(self.manifest.get("components", [])) | new)})
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
    """--from-source: build Kanban.app from the kit's source and copy it to ~/Applications (replacing an existing copy).

    Returns False when a prerequisite is missing or a step fails. Never runs remote scripts: missing tools are
    reported with the commands for the user to run."""
    dest = app_dest()
    build = ["cargo", "tauri", "build", "--bundles", "app"]
    print("Kanban desktop app (from source):")
    print(f"  1. (cd {APP_DIR} && {' '.join(build)})")
    print(f"  2. copy {APP_BUNDLE} -> {dest} (replaces an existing copy)")
    print("  Note: the build uses the system Python (the default config bundles no Python runtime); for the bundled"
          " runtime run app/scripts/fetch-python.sh and build with --config src-tauri/tauri.release.conf.json"
          " yourself.")
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


# ==================================================================== Kanban desktop app download (ADR-003)
class DownloadRefused(Exception):
    """A URL or redirect outside the allow-list, or too many redirects: nothing is fetched from it."""


def app_dest() -> Path:
    return Path.home() / "Applications" / "Kanban.app"


def check_url(url: str, loopback_ok: bool) -> None:
    """Every request and redirect hop: HTTPS to an allow-listed host (default port, no credentials); plain loopback
    (http or https) only under the test override."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    shown = f"{parts.scheme}://{parts.netloc}{parts.path}"
    if parts.username or parts.password:
        raise DownloadRefused(f"refused {shown}: credentials in the URL")
    if loopback_ok and host in LOOPBACK_HOSTS and parts.scheme in ("http", "https"):
        return
    try:
        port = parts.port
    except ValueError:
        port = -1
    if parts.scheme != "https" or host not in ALLOWED_HOSTS or port not in (None, 443):
        raise DownloadRefused(f"refused {shown}: downloads only follow HTTPS to {', '.join(ALLOWED_HOSTS)}")


class _AllowListRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, loopback_ok: bool):
        self.loopback_ok = loopback_ok

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        hops = getattr(req, "agentic_kit_hops", 0) + 1
        if hops > MAX_HOPS:
            at = urllib.parse.urlsplit(newurl).netloc
            raise DownloadRefused(f"refused: more than {MAX_HOPS} redirects (at {at})")
        check_url(newurl, self.loopback_ok)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.agentic_kit_hops = hops
        return new


def fetch(url: str, loopback_ok: bool, dest: Path | None = None):
    """GET url through the allow-listed redirect handler. Returns the body, or with `dest` streams it there and
    returns its sha256. Raises DownloadRefused, urllib.error.HTTPError / URLError or OSError."""
    check_url(url, loopback_ok)
    handlers = [_AllowListRedirects(loopback_ok)]
    if loopback_ok:
        handlers.append(urllib.request.ProxyHandler({}))  # the loopback test server is never reached via a proxy
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=HTTP_TIMEOUT) as resp:
        if dest is None:
            return resp.read()
        digest = hashlib.sha256()
        with open(dest, "wb") as out:
            for chunk in iter(lambda: resp.read(1 << 16), b""):
                digest.update(chunk)
                out.write(chunk)
        return digest.hexdigest()


def parse_repo(url: str) -> tuple[str, str] | None:
    """(owner, repo) of a github.com repository URL (https, or the git@github.com: form of a clone's origin)."""
    m = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                     r"([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]+?)(?:\.git)?/?", url.strip())
    if not m or m.group(2) in (".", ".."):
        return None
    return m.group(1), m.group(2)


def redact_url(url: str) -> str:
    """A URL safe to print: any user:password@ (a clone's origin can carry a token) becomes ***@."""
    return re.sub(r"(?<=://)[^/@\s]+@", "***@", url)


def git_origin() -> str | None:
    try:
        url = subprocess.run(["git", "-C", str(KIT), "remote", "get-url", "origin"], capture_output=True,
                             text=True, check=True, timeout=30).stdout.strip()
    except Exception:
        return None
    return url or None


def release_repo() -> str:
    return os.environ.get("AGENTIC_KIT_REPO", "").strip() or git_origin() or DEFAULT_REPO


def loopback_override(url: str) -> str | None:
    """AGENTIC_KIT_RELEASES_URL is honoured only for a loopback host; returns it without the trailing slash."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme in ("http", "https") and (parts.hostname or "").lower() in LOOPBACK_HOSTS \
            and not parts.username and not parts.password:
        return url.strip().rstrip("/")
    return None


def load_lock(path: Path = APP_LOCK) -> dict:
    """Pinned sums: {tag: {asset: sha256}}; keys starting with `_` are comments. Raises ValueError when invalid."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    pins = {}
    for tag, assets in data.items():
        if tag.startswith("_"):
            continue
        if not isinstance(assets, dict) or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
                                                   for v in assets.values()):
            raise ValueError(f"entry {tag!r} must map asset names to lowercase 64-hex sha256 values")
        pins[tag] = assets
    return pins


def parse_sums(text: str) -> dict[str, str]:
    """`shasum -a 256` output → {asset: sha256}."""
    sums = {}
    for line in text.splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(\S.*)", line.strip())
        if m:
            sums[m.group(2).strip()] = m.group(1).lower()
    return sums


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(m.group()) if (m := re.match(r"\d+", part)) else 0 for part in version.split("."))


def newest_release(releases) -> str | None:
    """The highest published (not draft, not prerelease) kanban-vX.Y.Z tag in a GitHub releases listing."""
    best = None
    for rel in releases if isinstance(releases, list) else []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name", ""))
        if not re.fullmatch(r"kanban-v\d+\.\d+\.\d+", tag):
            continue
        if best is None or version_key(tag[8:]) > version_key(best[8:]):
            best = tag
    return best


def installed_app_version(app: Path) -> str | None:
    try:
        with open(app / "Contents/Info.plist", "rb") as fh:
            version = plistlib.load(fh).get("CFBundleShortVersionString")
    except Exception:
        return None
    return version if isinstance(version, str) else None


def _ps(column: str) -> dict:
    """pid → the `ps -axo pid=,<column>=` value (empty when ps is unavailable)."""
    try:
        out = subprocess.run(["ps", "-axo", f"pid=,{column}="], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    rows = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            rows[parts[0]] = parts[1]
    return rows


def running_under(app: Path) -> dict:
    """Processes whose executable is inside `app`, by role: "app" (the app itself), "daemon" (pid → daemon.py path
    of a board daemon on the bundled Python) and "other" (pids of the plugin's MCP servers and hooks in Claude
    sessions: they run the interpreter by path, so a swapped bundle serves them after a restart; they never block)."""
    prefixes = tuple({str(app) + "/", str(app.resolve()) + "/"})
    macos = tuple(p + "Contents/MacOS/" for p in prefixes)
    found = {"app": [], "daemon": {}, "other": []}
    args = None
    for pid, exe in _ps("comm").items():
        if not exe.startswith(prefixes):
            continue
        if exe.startswith(macos):
            found["app"].append(pid)
            continue
        args = _ps("args") if args is None else args
        script = next((a for a in args.get(pid, "").split() if a.endswith("/daemon.py")), None)
        if script:
            found["daemon"][pid] = script
        else:
            found["other"].append(pid)
    return found


def download_kanban_app(dry: bool, update: bool = False, yes: bool = False) -> tuple[bool, dict | None]:
    """Download, verify and install Kanban.app into ~/Applications. Returns (ok, manifest record or None).
    Never executes anything it downloads; the installed app is untouched unless every check passed."""
    dest = app_dest()
    print("Kanban desktop app (download):")
    if sys.platform != "darwin":
        print("  Kanban desktop app: skipped (macOS only).")
        return dry, None
    repo = release_repo()
    parsed = parse_repo(repo)
    if not parsed:
        print(f"  Skipped ({redact_url(repo)}): downloads need a github.com repository; use --from-source")
        return dry, None
    owner, name = parsed
    base = f"https://github.com/{owner}/{name}/releases"
    api = f"https://api.github.com/repos/{owner}/{name}/releases?per_page=100"
    loopback = False
    override = os.environ.get("AGENTIC_KIT_RELEASES_URL", "").strip()
    if override:
        local = loopback_override(override)
        if local:
            base, api, loopback = local, local + "/api/releases?per_page=100", True
            print(f"  Note: releases from the local test server {local} (AGENTIC_KIT_RELEASES_URL).")
        else:
            print("  Note: AGENTIC_KIT_RELEASES_URL ignored (only 127.0.0.1, ::1 or localhost are allowed).")
    try:
        pins = load_lock()
    except ValueError as exc:
        print(f"  Download refused: {APP_LOCK_REL} is not valid ({exc}); nothing downloaded.")
        return False, None
    version = json.loads(PLUGIN_JSON.read_text())["version"]
    tag = f"kanban-v{version}"

    def sums_note(t: str) -> str:
        return (f"verified with the sums pinned in {APP_LOCK_REL}" if t in pins
                else "the release's SHA256SUMS (integrity only: sums not pinned in this kit)")

    print(f"  Repository: https://github.com/{owner}/{name}")
    print(f"  Release:    {tag} (if it is not published: the newest published kanban-v* listed by {api})")
    print(f"  Fetch:      {base}/download/{tag}/SHA256SUMS")
    print(f"              {base}/download/{tag}/{APP_ZIP}")
    print(f"  Install:    unpack in {dest.parent}/.Kanban.app.staging-<pid>/, check Kanban.app and its bundle id, "
          f"then replace {dest}")
    if dry:
        print(f"  Sums:       {sums_note(tag)}")
        print("  Dry run: nothing downloaded or installed.")
        return True, None
    installed = installed_app_version(dest)
    if update and installed and version_key(installed) >= version_key(version):
        print(f"  Kanban.app {installed} is up to date ({tag}); nothing downloaded.")
        return True, None
    tmp = Path(tempfile.mkdtemp(prefix="agentic-kit-app-"))
    staging = None
    try:
        try:
            try:
                sums_text = fetch(f"{base}/download/{tag}/SHA256SUMS", loopback).decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                try:
                    releases = json.loads(fetch(api, loopback))
                except urllib.error.HTTPError as api_exc:
                    if api_exc.code in (403, 429):
                        print("  Skipped: GitHub API rate limit; try later or --from-source")
                        return False, None
                    if api_exc.code != 404:
                        raise
                    releases = []
                chosen = newest_release(releases)
                if not chosen:
                    print(f"  Skipped: no published Kanban release in {owner}/{name} ({tag} is not published); "
                          "use --from-source")
                    return False, None
                print(f"  Note: {tag} is not published (this kit's version is {version}); using {chosen}, the newest "
                      "published release.")
                tag = chosen
                if update and installed and version_key(installed) >= version_key(tag[8:]):
                    print(f"  Kanban.app {installed} is up to date ({tag}); nothing downloaded.")
                    return True, None
                sums_text = fetch(f"{base}/download/{tag}/SHA256SUMS", loopback).decode("utf-8", "replace")
            release_sums = parse_sums(sums_text)
            if APP_ZIP not in release_sums:
                print(f"  Download refused: asset not in SHA256SUMS ({APP_ZIP}, {tag}).")
                return False, None
            if tag in pins:
                want = pins[tag].get(APP_ZIP)
                if not want:
                    print(f"  Download refused: {APP_LOCK_REL} pins {tag} but not {APP_ZIP}.")
                    return False, None
                if release_sums[APP_ZIP] != want:
                    print(f"  Download refused: the release's SHA256SUMS disagrees with {APP_LOCK_REL} for {tag}.")
                    return False, None
            else:
                want = release_sums[APP_ZIP]
            print(f"  Sums:       {sums_note(tag)}")
            zip_path = tmp / APP_ZIP
            got = fetch(f"{base}/download/{tag}/{APP_ZIP}", loopback, dest=zip_path)
        except DownloadRefused as exc:
            print(f"  Download {exc}; nothing installed.")
            return False, None
        except urllib.error.HTTPError as exc:
            print(f"  FAILED: download failed (HTTP {exc.code}); nothing installed.")
            return False, None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"  FAILED: download failed ({exc}); nothing installed.")
            return False, None
        if got != want:
            print(f"  Download refused: checksum mismatch for {APP_ZIP} (expected {want}, got {got}); "
                  "nothing installed.")
            return False, None

        apps = dest.parent
        apps.mkdir(parents=True, exist_ok=True)
        for stale in [*apps.glob(".Kanban.app.staging-*"), *apps.glob(".Kanban.app.old-*")]:
            shutil.rmtree(stale, ignore_errors=True)  # leftovers of an interrupted run
        staging = apps / f".Kanban.app.staging-{os.getpid()}"
        staging.mkdir()
        res = subprocess.run(["ditto", "-x", "-k", str(zip_path), str(staging)], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"  FAILED: unpacking {APP_ZIP} failed ({res.stderr.strip()}); nothing installed.")
            return False, None
        top = sorted(p.name for p in staging.iterdir())
        new = staging / "Kanban.app"
        if top != ["Kanban.app"] or new.is_symlink() or not new.is_dir():
            found = ", ".join(top) or "nothing"
            print(f"  Download refused: the zip's top level must be exactly Kanban.app (found: {found}).")
            return False, None
        try:
            with open(new / "Contents/Info.plist", "rb") as fh:
                info = plistlib.load(fh)
        except Exception:
            info = {}
        expected_id = json.loads(APP_CONF.read_text())["identifier"]
        if info.get("CFBundleIdentifier") != expected_id:
            print(f"  Download refused: bundle id {info.get('CFBundleIdentifier')!r} is not {expected_id}; "
                  "nothing installed.")
            return False, None
        new_version = str(info.get("CFBundleShortVersionString", "unknown"))

        retry = f"python3 {KIT / 'install.py'} --only kanban-app"
        found = running_under(dest)
        while found["app"] or found["daemon"]:
            # the app and the board daemon block the swap; quitting the app does not stop the daemon (never killed)
            steps = []
            if found["app"]:
                steps.append(f"Quit Kanban (pid {', '.join(found['app'])})")
            for pid, script in found["daemon"].items():
                steps.append(f"stop the board daemon (pid {pid}): "
                             f"sh {Path(script).with_name('kpython')} {script} --stop")
            if yes or not sys.stdin.isatty():
                print(f"  Update deferred: Kanban is running from {dest}; nothing replaced. "
                      f"{'; '.join(steps)}; then run `{retry}`.")
                return False, None
            reply = input(f"  Kanban is running. {'; '.join(steps)}; then press Enter, or s to skip: ").strip().lower()
            if reply == "s":
                print(f"  Skipped: run `{retry}` after quitting Kanban.")
                return False, None
            found = running_under(dest)

        old = apps / f".Kanban.app.old-{os.getpid()}"
        had_old = dest.exists() or dest.is_symlink()
        if had_old:
            os.rename(dest, old)
        try:
            os.rename(new, dest)
        except OSError:
            if had_old:
                os.rename(old, dest)
            raise
        if old.is_symlink():
            old.unlink()
        else:
            shutil.rmtree(old, ignore_errors=True)
        print(f"  Installed Kanban.app {new_version} ({tag}) to {dest}.")
        if found["other"]:
            print(f"  Note: {len(found['other'])} Claude process(es) (the kanban plugin's MCP server or hooks) still run "
                  "the previous bundled Python; restart those Claude sessions to use the new one.")
        if Path("/Applications/Kanban.app").exists():
            print("  Note: /Applications/Kanban.app also exists; it was left alone (the plugin's kpython launcher"
                  " prefers ~/Applications).")
        return True, {"version": new_version, "tag": tag}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def marketplace_source() -> str:
    """GitHub owner/repo when the kit is a clone of a GitHub repo (shareable), else the local path."""
    url = git_origin()
    if not url:
        return str(KIT)
    for prefix in ("https://github.com/", "git@github.com:"):
        if url.startswith(prefix):
            return url[len(prefix):].removesuffix(".git")
    return url or str(KIT)


def ask_components() -> list[str]:
    print("Which parts do you want? (Enter = the default shown in brackets)\n")
    chosen = []
    for key, text in COMPONENTS.items():
        if key in MACOS_ONLY and sys.platform != "darwin":
            print(f"  {key:<12} skipped: the Kanban desktop app is macOS only\n")
            continue
        default = key in DEFAULT_ON or (key == "kanban-app" and "kanban" in chosen)  # pre-selected with kanban
        reply = input(f"  {key:<12} {text}\n  install? [{'Y/n' if default else 'y/N'}] ").strip().lower()
        if (reply in ("y", "yes")) or (reply == "" and default):
            chosen.append(key)
    return chosen


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="project folder to install into")
    ap.add_argument("--only", help="comma-separated components: " + ",".join(COMPONENTS))
    ap.add_argument("--all", action="store_true", help="all components")
    ap.add_argument("--yes", "-y", action="store_true", help="no questions (default components unless --only/--all)")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("--update", action="store_true", help="refresh kit files you have not edited (after git pull)")
    ap.add_argument("--kanban-scope", choices=["user", "project", "local"], default="user",
                    help="where the kanban plugin is enabled (default: user)")
    ap.add_argument("--from-source", action="store_true",
                    help="kanban-app: build Kanban.app with cargo tauri instead of downloading the release")
    ap.add_argument("--list", action="store_true", help="list the components and exit")
    return ap


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def app_skip_note() -> None:
    print("Kanban desktop app: skipped (macOS only).")


def select_components(args: argparse.Namespace) -> list[str]:
    """--only, else --all, else the manifest (--update), else the defaults (--yes / no TTY), else the menu.
    On macOS the app comes with --all (Q15) and follows `kanban` in the menu and under --yes (Q10); elsewhere it is
    skipped with a note."""
    mac = sys.platform == "darwin"
    only = [c.strip() for c in (args.only or "").split(",") if c.strip()]
    manifest_path = Path(args.target or ".").expanduser().resolve() / MANIFEST
    if only:
        return only
    if args.all:
        components = list(COMPONENTS)
    elif args.update and args.target and manifest_path.exists():
        components = list(json.loads(manifest_path.read_text()).get("components", DEFAULT_ON))
        if "kanban-app" not in components:
            return components
    elif args.yes or not sys.stdin.isatty():
        # the menu's defaults: the app follows `kanban` (pre-selected only when kanban is selected)
        components = DEFAULT_ON + (["kanban-app"] if "kanban" in DEFAULT_ON else [])
        if "kanban-app" not in components:
            return components
    else:
        return ask_components()
    if not mac:
        app_skip_note()
        components = [c for c in components if c not in MACOS_ONLY]
    return components


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()

    if args.list:
        for key, text in COMPONENTS.items():
            print(f"{key:<12} {text}")
        return 0
    only = [c.strip() for c in (args.only or "").split(",") if c.strip()]
    unknown = [c for c in only if c not in COMPONENTS]
    if unknown:
        ap.error(f"unknown component(s): {', '.join(unknown)}")
    if only and set(only) <= NO_TARGET and not args.target:  # the app needs no project folder
        if args.from_source:
            return 0 if build_kanban_app(args.dry_run) else 1
        return 0 if download_kanban_app(args.dry_run, yes=args.yes)[0] else 1
    if not args.target:
        ap.error("target project folder is required")
    target = Path(args.target).expanduser().resolve()
    if not target.is_dir():
        ap.error(f"not a folder: {target}")
    if target == KIT:
        ap.error("the target is the kit itself")

    components = select_components(args)
    if not components:
        print("Nothing selected.")
        return 0

    inst = Installer(target, args.dry_run, args.update, args.yes, args.from_source)
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
