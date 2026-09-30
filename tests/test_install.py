#!/usr/bin/env python3
"""Tests for install.py: selection, never-overwrite, skip-on-clash, settings merge, update, dry run, and the Kanban
desktop app download (ADR-003), which only ever talks to a loopback fake release (tests/fixtures/fake_release).

Run: python3 tests/test_install.py
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "fake_release"))
import server as fake  # noqa: E402  (tests/fixtures/fake_release/server.py)

APPROVE_TOOL = "mcp__plugin_kanban_kanban__kanban_approve"
TOKEN_DENY = "Read(~/Library/Application Support/Kanban/ui.token)"
MACOS = sys.platform == "darwin"
FORK = "https://github.com/alice/agentic-kit.git"


def run_raw(*args, env=None, kit=KIT):
    return subprocess.run([sys.executable, str(kit / "install.py"), *[str(a) for a in args]],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, env=env)


def run(target, *args, env=None, kit=KIT):
    res = run_raw(target, *args, env=env, kit=kit)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def bare_env(home: Path) -> dict:
    """No cargo, no claude: PATH has only the system folders and HOME has no ~/.cargo."""
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "LANG": "C.UTF-8"}


def app_env(home: Path, base: str | None = None, repo: str | None = FORK, path_first: Path | None = None) -> dict:
    """bare_env + the fork repository + (optionally) the loopback release override and a folder first on PATH."""
    env = bare_env(home)
    if repo is not None:
        env["AGENTIC_KIT_REPO"] = repo
    if base is not None:
        env["AGENTIC_KIT_RELEASES_URL"] = base + "/"
    if path_first is not None:
        env["PATH"] = f"{path_first}:{env['PATH']}"
    return env


def plugin_version(kit: Path = KIT) -> str:
    return json.loads((kit / "plugins/kanban/.claude-plugin/plugin.json").read_text())["version"]


def mini_kit(root: Path, version: str = "0.3.1", lock: dict | None = None) -> Path:
    """A copy of just the files the app download reads, so a test controls the plugin version and releases.lock
    without touching the kit. It is not a git clone: the repository comes from AGENTIC_KIT_REPO or the default."""
    kit = root / "kit"
    for rel in ("install.py", "plugins/kanban/app/src-tauri/tauri.conf.json"):
        (kit / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(KIT / rel, kit / rel)
    for rel in ("LICENSES", ".claude/agents"):
        shutil.copytree(KIT / rel, kit / rel)
    set_version(kit, version)
    lock_data = {"_format": "test"} if lock is None else lock
    (kit / "plugins/kanban/app/releases.lock").write_text(json.dumps(lock_data))
    return kit


def set_version(kit: Path, version: str) -> None:
    path = kit / "plugins/kanban/.claude-plugin/plugin.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "kanban", "version": version}))


def tree_digest(root: Path) -> str:
    """Hash of every path and file content under root (byte-identical check)."""
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        h.update(path.relative_to(root).as_posix().encode())
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


def installed_version(home: Path) -> str:
    info = home / "Applications/Kanban.app/Contents/Info.plist"
    return plistlib.loads(info.read_bytes())["CFBundleShortVersionString"]


def load_installer():
    spec = importlib.util.spec_from_file_location("kit_install", KIT / "install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_install_non_interactive(self):
        run(self.t, "--yes")
        for rel in (".claude/skills/sdd/SKILL.md", ".claude/skills/test-driven-development/SKILL.md",
                    ".claude/agents/architect.md", ".claude/hooks/guard_bash.py", ".claude/settings.json",
                    ".SDD/templates/prd.md", "CLAUDE.md", ".claude/memory/MEMORY.md",
                    ".claude/agentic-kit/LICENSES/NOTICE.md", ".claude/agentic-kit/installed.json"):
            self.assertTrue((self.t / rel).exists(), rel)
        self.assertFalse((self.t / "plugins").exists())
        hook = self.t / ".claude/hooks/test_guard_bash.sh"
        res = subprocess.run(["bash", str(hook)], cwd=self.t, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout[-400:])

    def test_only_selected_components(self):
        run(self.t, "--only", "agents")
        self.assertTrue((self.t / ".claude/agents/qa-verifier.md").exists())
        self.assertFalse((self.t / ".claude/skills").exists())
        self.assertFalse((self.t / ".SDD").exists())
        self.assertFalse((self.t / "CLAUDE.md").exists())

    def test_never_overwrites_and_skips_existing_names(self):
        (self.t / ".claude/skills/sdd").mkdir(parents=True)
        (self.t / ".claude/skills/sdd/SKILL.md").write_text("my own sdd")
        (self.t / ".claude/agents").mkdir(parents=True)
        (self.t / ".claude/agents/architect.md").write_text("my architect")
        (self.t / ".SDD/specs/feature-x").mkdir(parents=True)
        (self.t / ".SDD/README.md").write_text("my process")
        (self.t / "CLAUDE.md").write_text("my instructions")
        out = run(self.t, "--all", "--yes", "--dry-run")
        self.assertIn("DRY RUN", out)
        out = run(self.t, "--only", "sdd,skills,agents,instructions", "--yes")
        self.assertEqual((self.t / ".claude/skills/sdd/SKILL.md").read_text(), "my own sdd")
        self.assertFalse((self.t / ".claude/skills/sdd/reference").exists())  # whole skill skipped
        self.assertEqual((self.t / ".claude/agents/architect.md").read_text(), "my architect")
        self.assertTrue((self.t / ".claude/agents/qa-verifier.md").exists())
        self.assertEqual((self.t / ".SDD/README.md").read_text(), "my process")
        self.assertTrue((self.t / ".SDD/templates/adr.md").exists())       # missing templates added
        self.assertEqual((self.t / "CLAUDE.md").read_text(), "my instructions")
        kit_md = (self.t / ".claude/agentic-kit/CLAUDE.kit.md").read_text()
        self.assertIn("@../memory/MEMORY.md", kit_md)  # import path fixed for the new location
        self.assertIn("skipped: you already have this skill", out)

    def test_settings_merge_is_additive_with_backup(self):
        (self.t / ".claude").mkdir()
        mine = {"model": "sonnet", "permissions": {"deny": ["Bash(rm -rf *)"], "allow": ["Bash(npm *)"]},
                "hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "fmt"}]}]}}
        (self.t / ".claude/settings.json").write_text(json.dumps(mine))
        run(self.t, "--only", "guard", "--yes")
        merged = json.loads((self.t / ".claude/settings.json").read_text())
        self.assertEqual(merged["model"], "sonnet")
        self.assertEqual(merged["permissions"]["allow"], ["Bash(npm *)"])
        self.assertEqual(merged["permissions"]["deny"][0], "Bash(rm -rf *)")
        self.assertIn("Bash(git push *)", merged["permissions"]["deny"])
        self.assertIn("PostToolUse", merged["hooks"])
        self.assertEqual(len(merged["hooks"]["PreToolUse"]), 1)
        self.assertEqual(len(list((self.t / ".claude").glob("settings.json.bak-*"))), 1)
        run(self.t, "--only", "guard", "--yes")  # idempotent: no duplicates, no new backup
        again = json.loads((self.t / ".claude/settings.json").read_text())
        self.assertEqual(again, merged)
        self.assertEqual(len(list((self.t / ".claude").glob("settings.json.bak-*"))), 1)

    def test_update_refreshes_only_unedited_kit_files(self):
        run(self.t, "--only", "skills,agents", "--yes")
        manifest = json.loads((self.t / ".claude/agentic-kit/installed.json").read_text())
        edited = self.t / ".claude/agents/architect.md"
        edited.write_text("edited by the team")
        untouched = self.t / ".claude/agents/qa-verifier.md"
        # simulate an older kit version of the untouched file
        untouched.write_text("old kit text")
        manifest["files"][".claude/agents/qa-verifier.md"] = __import__("hashlib").sha256(b"old kit text").hexdigest()
        (self.t / ".claude/agentic-kit/installed.json").write_text(json.dumps(manifest))
        out = run(self.t, "--update", "--yes")
        self.assertEqual(edited.read_text(), "edited by the team")
        self.assertEqual(untouched.read_text(), (KIT / ".claude/agents/qa-verifier.md").read_text())
        self.assertIn("updated", out)

    def test_symlinked_skills_folder_is_respected(self):
        real = self.t / "shared-skills"
        (real / "sdd").mkdir(parents=True)
        (real / "sdd/SKILL.md").write_text("linked sdd")
        (self.t / ".claude").mkdir()
        (self.t / ".claude/skills").symlink_to(real)
        run(self.t, "--only", "sdd,skills", "--yes")
        self.assertEqual((real / "sdd/SKILL.md").read_text(), "linked sdd")
        self.assertTrue((real / "systematic-debugging/SKILL.md").exists())

    def test_kanban_dry_run_prints_commands(self):
        out = run(self.t, "--only", "kanban", "--dry-run")
        self.assertIn("claude plugin marketplace add", out)
        self.assertIn("claude plugin install kanban@agentic-kit --scope user", out)

    def test_settings_merge_adds_approve_ask_and_token_deny(self):
        # fresh project: the settings file the guard writes has both rules
        run(self.t, "--only", "guard", "--yes")
        fresh = json.loads((self.t / ".claude/settings.json").read_text())
        self.assertIn(APPROVE_TOOL, fresh["permissions"]["ask"])
        self.assertIn(TOKEN_DENY, fresh["permissions"]["deny"])
        self.assertIn("Bash(git push *)", fresh["permissions"]["deny"])
        self.assertEqual(len(fresh["hooks"]["PreToolUse"]), 1)
        # existing settings: merged additively, once
        other = self.t / "other"
        (other / ".claude").mkdir(parents=True)
        (other / ".claude/settings.json").write_text(json.dumps({"permissions": {"ask": ["Bash(make *)"]}}))
        run(other, "--only", "guard", "--yes")
        run(other, "--only", "guard", "--yes")
        merged = json.loads((other / ".claude/settings.json").read_text())
        self.assertEqual(merged["permissions"]["ask"][0], "Bash(make *)")
        self.assertEqual(merged["permissions"]["ask"].count(APPROVE_TOOL), 1)
        self.assertEqual(merged["permissions"]["deny"].count(TOKEN_DENY), 1)
        # the kanban component alone (no claude on PATH: commands are only printed) adds the two rules, no hook
        third = self.t / "third"
        third.mkdir()
        home = self.t / "home"
        home.mkdir()
        run(third, "--only", "kanban", "--yes", env=bare_env(home))
        only_kanban = json.loads((third / ".claude/settings.json").read_text())
        self.assertEqual(only_kanban["permissions"], {"ask": [APPROVE_TOOL], "deny": [TOKEN_DENY]})
        self.assertNotIn("hooks", only_kanban)
        run(third, "--only", "guard", "--yes")  # the guard later adds its rules and hook to that file
        both = json.loads((third / ".claude/settings.json").read_text())
        self.assertEqual(both["permissions"]["ask"].count(APPROVE_TOOL), 1)
        self.assertEqual(len(both["hooks"]["PreToolUse"]), 1)


# ==================================================================== Kanban desktop app (v0.3.1 PRD-02, ADR-003)
ON_MAC = unittest.skipUnless(MACOS, "the Kanban app installs on macOS only (ditto, ~/Applications)")


class KanbanAppTests(unittest.TestCase):
    """Every download goes to a loopback FakeRelease through the loopback-only AGENTIC_KIT_RELEASES_URL override, into
    a temp HOME; the kit itself is copied (mini_kit) whenever a test needs its own plugin version or releases.lock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.home = self.t / "home"
        self.home.mkdir()
        self.proj = self.t / "proj"
        self.proj.mkdir()
        self.apps = self.home / "Applications"

    def tearDown(self):
        self.tmp.cleanup()

    def zip_bytes(self, version="0.3.1", **kw) -> bytes:
        out = self.t / f"zip-{version}-{len(list(self.t.glob('zip-*')))}.zip"
        return fake.make_zip(out, version, **kw)

    def old_app(self, version="0.3.0") -> str:
        """An installed Kanban.app in the temp HOME; returns its digest."""
        fake.make_app(self.apps, version)
        return tree_digest(self.apps / "Kanban.app")

    def manifest(self) -> dict:
        return json.loads((self.proj / ".claude/agentic-kit/installed.json").read_text())

    def install_app(self, kit, rel, *extra, env=None):
        res = run_raw(self.proj, "--only", "kanban-app", "--yes", *extra, env=env or app_env(self.home, rel.base),
                      kit=kit)
        return res, res.stdout + res.stderr

    # ---------------------------------------------------------------- download, verify, install
    @ON_MAC
    def test_app_download_happy_path(self):
        kit = mini_kit(self.t)
        self.old_app("0.3.0")
        (self.apps / ".Kanban.app.staging-1").mkdir()  # stale leftovers from a crash are removed
        (self.apps / ".Kanban.app.old-2").mkdir()
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", self.zip_bytes("0.3.1"))
            res, out = self.install_app(kit, rel)
        self.assertEqual(res.returncode, 0, out)
        self.assertEqual(installed_version(self.home), "0.3.1")
        self.assertEqual(sorted(p.name for p in self.apps.iterdir()), ["Kanban.app"])  # no staging, no old copy
        self.assertIn("integrity only: sums not pinned in this kit", out)
        self.assertIn("https://github.com/alice/agentic-kit", out)
        manifest = self.manifest()
        self.assertIn("kanban-app", manifest["components"])
        self.assertEqual(manifest["kanban_app"]["version"], "0.3.1")
        self.assertEqual(manifest["kanban_app"]["tag"], "kanban-v0.3.1")
        self.assertFalse((self.proj / ".claude/agentic-kit/LICENSES").exists())  # the app copies nothing in

    @ON_MAC
    def test_app_installs_without_a_project_and_creates_applications(self):
        kit = mini_kit(self.t)
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", self.zip_bytes("0.3.1"))
            res = run_raw("--only", "kanban-app", env=app_env(self.home, rel.base), kit=kit)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(installed_version(self.home), "0.3.1")

    @ON_MAC
    def test_app_pinned_sums_preferred(self):
        data = self.zip_bytes("0.3.1")
        kit = mini_kit(self.t, lock={"_format": "x", "kanban-v0.3.1": {"Kanban.app.zip": fake.sha256(data),
                                                                         "Kanban.dmg": "a" * 64}})
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", data)
            res, out = self.install_app(kit, rel)
        self.assertEqual(res.returncode, 0, out)
        self.assertIn("sums pinned in plugins/kanban/app/releases.lock", out)
        self.assertNotIn("integrity only", out)
        self.assertEqual(installed_version(self.home), "0.3.1")

    @ON_MAC
    def test_app_pinned_mismatch_refused(self):
        data = self.zip_bytes("0.3.1")
        kit = mini_kit(self.t, lock={"kanban-v0.3.1": {"Kanban.app.zip": "0" * 64}})
        before = self.old_app("0.3.0")
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", data)  # the release's own SHA256SUMS matches the zip, the pin does not
            res, out = self.install_app(kit, rel)
            self.assertEqual(rel.asset_requests(), [])  # refused before the zip is fetched
        self.assertNotEqual(res.returncode, 0, out)
        self.assertIn("disagrees with plugins/kanban/app/releases.lock", out)
        self.assertEqual(tree_digest(self.apps / "Kanban.app"), before)
        self.assertNotIn("kanban-app", self.manifest().get("components", []))

    @ON_MAC
    def test_app_pinned_entry_but_zip_differs_refused(self):
        good, evil = self.zip_bytes("0.3.1"), self.zip_bytes("6.6.6")
        kit = mini_kit(self.t, lock={"kanban-v0.3.1": {"Kanban.app.zip": fake.sha256(good)}})
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", evil, sums=fake.sums_text({"Kanban.app.zip": good}))
            res, out = self.install_app(kit, rel)
        self.assertNotEqual(res.returncode, 0, out)
        self.assertIn("checksum mismatch", out)
        self.assertFalse((self.apps / "Kanban.app").exists())

    @ON_MAC
    def test_app_bad_sum_refused(self):
        kit = mini_kit(self.t)
        before = self.old_app("0.3.0")
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", self.zip_bytes("0.3.1"), sums=fake.sums_text({"Kanban.app.zip": b"other"}))
            res, out = self.install_app(kit, rel)
        self.assertNotEqual(res.returncode, 0, out)
        self.assertIn("checksum mismatch", out)
        self.assertEqual(tree_digest(self.apps / "Kanban.app"), before)
        self.assertEqual(sorted(p.name for p in self.apps.iterdir()), ["Kanban.app"])
        self.assertNotIn("kanban-app", self.manifest().get("components", []))

    @ON_MAC
    def test_app_asset_missing_from_sums_refused(self):
        kit = mini_kit(self.t)
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", self.zip_bytes("0.3.1"), sums=fake.sums_text({"Kanban.dmg": b"x"}))
            res, out = self.install_app(kit, rel)
        self.assertNotEqual(res.returncode, 0, out)
        self.assertIn("asset not in SHA256SUMS", out)

    @ON_MAC
    def test_app_unparsable_lock_refused(self):
        kit = mini_kit(self.t)
        (kit / "plugins/kanban/app/releases.lock").write_text("{not json")
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", self.zip_bytes("0.3.1"))
            res, out = self.install_app(kit, rel)
            self.assertEqual(rel.log, [])
        self.assertNotEqual(res.returncode, 0, out)
        self.assertIn("releases.lock", out)
        self.assertFalse(self.apps.exists())

    @ON_MAC
    def test_app_wrong_bundle_refused(self):
        kit = mini_kit(self.t)
        before = self.old_app("0.3.0")
        cases = {"bundle id": dict(bundle_id="com.example.other"),
                 "top level": dict(extra_top_level="README.txt"),
                 "Kanban.app": dict(top_name="Other.app")}
        for expected, kw in cases.items():
            with self.subTest(expected), fake.FakeRelease() as rel:
                rel.release("kanban-v0.3.1", self.zip_bytes("0.3.1", **kw))
                res, out = self.install_app(kit, rel)
                self.assertNotEqual(res.returncode, 0, out)
                self.assertIn("refused", out)
                self.assertIn(expected, out)
                self.assertEqual(tree_digest(self.apps / "Kanban.app"), before)
                self.assertEqual(sorted(p.name for p in self.apps.iterdir()), ["Kanban.app"])

    @ON_MAC
    def test_app_running_deferred(self):
        kit = mini_kit(self.t)
        before = self.old_app("0.3.0")
        bin_dir = self.t / "bin"
        bin_dir.mkdir()
        exe = self.apps / "Kanban.app/Contents/Resources/python/arm64/bin/python3"
        (bin_dir / "ps").write_text(f"#!/bin/sh\necho '  1 /sbin/launchd'\necho '4242 {exe}'\n")
        (bin_dir / "ps").chmod(0o755)
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", self.zip_bytes("0.3.1"))
            res, out = self.install_app(kit, rel, env=app_env(self.home, rel.base, path_first=bin_dir))
        self.assertNotEqual(res.returncode, 0, out)
        self.assertIn("deferred", out)
        self.assertIn("install.py --only kanban-app", out)
        self.assertEqual(tree_digest(self.apps / "Kanban.app"), before)
        self.assertEqual(sorted(p.name for p in self.apps.iterdir()), ["Kanban.app"])

    # ---------------------------------------------------------------- redirects and hosts
    @ON_MAC
    def test_app_foreign_host_refused(self):
        kit = mini_kit(self.t)
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", b"")
            rel.redirect("/download/kanban-v0.3.1/Kanban.app.zip", "https://evil.example/Kanban.app.zip")
            res, out = self.install_app(kit, rel)
        self.assertNotEqual(res.returncode, 0, out)
        self.assertIn("refused", out)
        self.assertIn("evil.example", out)
        self.assertFalse(self.apps.exists())  # nothing written

    @ON_MAC
    def test_app_https_downgrade_refused(self):
        kit = mini_kit(self.t)
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", b"")
            rel.redirect("/download/kanban-v0.3.1/Kanban.app.zip", "http://objects.githubusercontent.com/x.zip")
            res, out = self.install_app(kit, rel)
        self.assertNotEqual(res.returncode, 0, out)
        self.assertIn("refused", out)
        self.assertIn("http://objects.githubusercontent.com", out)
        self.assertFalse(self.apps.exists())

    @ON_MAC
    def test_app_hop_limit(self):
        kit = mini_kit(self.t)
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", b"")
            rel.redirect("/download/kanban-v0.3.1/Kanban.app.zip", rel.base + "/hop/1")
            for i in range(1, 6):
                rel.redirect(f"/hop/{i}", rel.base + f"/hop/{i + 1}")  # the 6th redirect goes to /hop/6
            rel.ok("/hop/6", b"never served")
            res, out = self.install_app(kit, rel)
            self.assertNotIn("/hop/6", rel.log)
        self.assertNotEqual(res.returncode, 0, out)
        self.assertIn("more than 5 redirects", out)
        self.assertFalse(self.apps.exists())

    def test_redirect_handler_unit(self):
        inst = load_installer()
        allowed = ["https://github.com/a/b/releases/download/t/x", "https://api.github.com/repos/a/b/releases",
                   "https://objects.githubusercontent.com/x", "https://release-assets.githubusercontent.com/x"]
        for url in allowed:
            inst.check_url(url, loopback_ok=False)
        for url in ("http://github.com/x", "https://evil.example/x", "https://github.com.evil.example/x",
                    "https://user@evil.example/x", "ftp://github.com/x", "http://127.0.0.1:1/x"):
            with self.subTest(url), self.assertRaises(inst.DownloadRefused):
                inst.check_url(url, loopback_ok=False)
        inst.check_url("http://127.0.0.1:1/x", loopback_ok=True)
        inst.check_url("http://localhost:1/x", loopback_ok=True)
        inst.check_url("http://[::1]:1/x", loopback_ok=True)
        with self.assertRaises(inst.DownloadRefused):
            inst.check_url("http://evil.example/x", loopback_ok=True)  # http stays loopback-only
        with fake.FakeRelease() as rel:  # exactly 5 hops is fine, the 6th is refused
            rel.redirect("/a/0", rel.base + "/a/1")
            for i in range(1, 5):
                rel.redirect(f"/a/{i}", rel.base + f"/a/{i + 1}")
            rel.ok("/a/5", b"five")
            self.assertEqual(inst.fetch(rel.base + "/a/0", loopback_ok=True), b"five")
            rel.redirect("/b/0", rel.base + "/a/0")
            with self.assertRaises(inst.DownloadRefused):
                inst.fetch(rel.base + "/b/0", loopback_ok=True)
            self.assertEqual(rel.log.count("/a/5"), 1)
            self.assertTrue(all(h == "agentic-kit-installer" for h in rel.user_agents))

    def test_app_override_loopback_only(self):
        inst = load_installer()
        for url in ("https://evil.example/", "http://127.0.0.1.evil.example/", "http://10.0.0.1/"):
            with self.subTest(url):
                self.assertIsNone(inst.loopback_override(url))
        self.assertEqual(inst.loopback_override("http://127.0.0.1:8123/"), "http://127.0.0.1:8123")
        self.assertEqual(inst.loopback_override("http://localhost:1/r"), "http://localhost:1/r")
        self.assertEqual(inst.loopback_override("http://[::1]:1/"), "http://[::1]:1")
        env = app_env(self.home)
        env["AGENTIC_KIT_RELEASES_URL"] = "https://evil.example/"
        out = run_raw("--only", "kanban-app", "--dry-run", env=env).stdout
        if MACOS:
            self.assertIn("AGENTIC_KIT_RELEASES_URL ignored", out)
            self.assertIn("https://github.com/alice/agentic-kit/releases/download/", out)
        self.assertNotIn("https://evil.example/download", out)

    @ON_MAC
    def test_app_dry_run_makes_no_request(self):
        kit = mini_kit(self.t)
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", self.zip_bytes("0.3.1"))
            res = run_raw("--only", "kanban-app", "--dry-run", env=app_env(self.home, rel.base), kit=kit)
            res2 = run_raw(self.proj, "--all", "--yes", "--dry-run", env=app_env(self.home, rel.base))
            self.assertEqual(rel.log, [])
        out = res.stdout + res.stderr
        self.assertEqual(res.returncode, 0, out)
        self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)
        for text in ("https://github.com/alice/agentic-kit", "kanban-v0.3.1", rel.base + "/download/kanban-v0.3.1/"
                     "Kanban.app.zip", "SHA256SUMS", "integrity only: sums not pinned in this kit",
                     str(self.apps / "Kanban.app")):
            self.assertIn(text, out)
        self.assertNotIn("cargo tauri", out)
        self.assertFalse(self.apps.exists())
        self.assertFalse((self.proj / ".claude").exists())

    @ON_MAC
    def test_app_base_url_from_repo(self):
        kit = mini_kit(self.t)
        cases = {"https://github.com/alice/agentic-kit.git": "https://github.com/alice/agentic-kit/releases/download/"
                                                            "kanban-v0.3.1/Kanban.app.zip",
                 "https://github.com/alice/agentic-kit": "https://github.com/alice/agentic-kit/releases/download/",
                 None: "https://github.com/yasinishyn/agentic-kit/releases/download/"}  # mini kit has no origin
        for repo, expected in cases.items():
            with self.subTest(repo):
                res = run_raw("--only", "kanban-app", "--dry-run", env=app_env(self.home, repo=repo), kit=kit)
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertIn(expected, res.stdout)
        for repo in ("https://gitlab.com/alice/agentic-kit.git", "https://github.com/alice/agentic-kit/tree/main",
                     "http://github.com/alice/agentic-kit"):
            with self.subTest(repo):
                res = run_raw(self.proj, "--only", "agents,kanban-app", "--yes", env=app_env(self.home, repo=repo),
                              kit=kit)
                out = res.stdout + res.stderr
                self.assertIn("downloads need a github.com repository; use --from-source", out)
                self.assertNotEqual(res.returncode, 0, out)
                self.assertTrue((self.proj / ".claude/agents/architect.md").exists())  # others continue
        inst = load_installer()
        self.assertEqual(inst.parse_repo("https://github.com/alice/agentic-kit.git"), ("alice", "agentic-kit"))
        self.assertEqual(inst.parse_repo("git@github.com:alice/agentic-kit.git"), ("alice", "agentic-kit"))
        self.assertIsNone(inst.parse_repo("https://github.com/alice/agentic-kit/../x"))

    # ---------------------------------------------------------------- release choice
    @ON_MAC
    def test_app_fallback_latest_ignores_prereleases(self):
        kit = mini_kit(self.t, version="0.4.0")
        with fake.FakeRelease() as rel:
            rel.api(fake.releases_api())  # newest published non-prerelease kanban-v*: 0.3.1
            rel.release("kanban-v0.3.1", self.zip_bytes("0.3.1"))
            res, out = self.install_app(kit, rel)
            self.assertNotIn("/download/kanban-v0.9.0/SHA256SUMS", rel.log)
        self.assertEqual(res.returncode, 0, out)
        self.assertIn("kanban-v0.4.0 is not published", out)
        self.assertIn("using kanban-v0.3.1", out)
        self.assertEqual(installed_version(self.home), "0.3.1")
        self.assertEqual(self.manifest()["kanban_app"]["tag"], "kanban-v0.3.1")

    @ON_MAC
    def test_app_no_release(self):
        kit = mini_kit(self.t, version="0.4.0")
        only_unpublished = [r for r in fake.releases_api() if r["draft"] or r["prerelease"]]
        for releases in ([], only_unpublished):
            with self.subTest(len(releases)), fake.FakeRelease() as rel:
                rel.api(releases)
                res = run_raw(self.proj, "--only", "agents,kanban-app", "--yes", env=app_env(self.home, rel.base),
                              kit=kit)
                out = res.stdout + res.stderr
                self.assertNotEqual(res.returncode, 0, out)
                self.assertIn("no published Kanban release", out)
                self.assertIn("--from-source", out)
                self.assertTrue((self.proj / ".claude/agents/architect.md").exists())
                self.assertNotIn("kanban-app", self.manifest()["components"])
                self.assertFalse(self.apps.exists())

    @ON_MAC
    def test_app_api_rate_limited(self):
        kit = mini_kit(self.t, version="0.4.0")
        for code in (403, 429):
            with self.subTest(code), fake.FakeRelease() as rel:
                rel.status("/api/releases?per_page=100", code)
                res = run_raw(self.proj, "--only", "agents,kanban-app", "--yes", env=app_env(self.home, rel.base),
                              kit=kit)
                out = res.stdout + res.stderr
                self.assertNotEqual(res.returncode, 0, out)
                self.assertIn("GitHub API rate limit; try later or --from-source", out)
                self.assertNotIn("kanban-app", self.manifest()["components"])
                self.assertIn("agents", self.manifest()["components"])  # other components continue

    # ---------------------------------------------------------------- selection
    def test_app_in_all_and_yes(self):
        inst = load_installer()
        # --yes takes the menu defaults: the app follows `kanban`, which is not a default component (Q10)
        with mock.patch.object(inst.sys, "platform", "darwin"):
            self.assertEqual(inst.select_components(inst.parse_args([str(self.proj), "--yes"])), inst.DEFAULT_ON)
            self.assertNotIn("kanban", inst.DEFAULT_ON)
            self.assertEqual(inst.select_components(inst.parse_args([str(self.proj), "--only", "kanban-app"])),
                             ["kanban-app"])
        with fake.FakeRelease() as rel:  # --yes without kanban: no app, no request
            rel.release("kanban-v" + plugin_version(), self.zip_bytes(plugin_version()))
            out = run(self.proj, "--yes", env=app_env(self.home, rel.base))
            self.assertEqual(rel.log, [])
        self.assertNotIn("Kanban desktop app", out)
        self.assertFalse(self.apps.exists())
        self.assertNotIn("kanban-app", self.manifest()["components"])
        for flags in (["--all"], ["--all", "--yes"]):
            with self.subTest(flags):
                with mock.patch.object(inst.sys, "platform", "darwin"):
                    self.assertIn("kanban-app", inst.select_components(inst.parse_args([str(self.proj), *flags])))
                with mock.patch.object(inst.sys, "platform", "linux"), contextlib.redirect_stdout(io.StringIO()) as o:
                    self.assertNotIn("kanban-app", inst.select_components(inst.parse_args([str(self.proj), *flags])))
                self.assertIn("skipped (macOS only)", o.getvalue())
        with mock.patch.object(inst.sys, "platform", "linux"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), mock.patch.object(inst.sys, "argv",
                                                                    ["install.py", str(self.proj), "--all",
                                                                     "--dry-run"]):
                self.assertEqual(inst.main(), 0)
        self.assertIn("Kanban desktop app: skipped (macOS only)", buf.getvalue())
        list_out = run_raw("--list").stdout
        self.assertIn("kanban-app", list_out)
        self.assertIn("kanban-app", run_raw("--help").stdout)
        self.assertIn("--from-source", run_raw("--help").stdout)

    def test_app_menu_default_on_macos(self):
        inst = load_installer()

        def answers(kanban_reply):
            def fake_input(prompt):
                return kanban_reply if prompt.lstrip().startswith("kanban ") else ""
            return fake_input

        with mock.patch.object(inst.sys, "platform", "darwin"), contextlib.redirect_stdout(io.StringIO()):
            with mock.patch("builtins.input", answers("y")):
                self.assertIn("kanban-app", inst.ask_components())
            with mock.patch("builtins.input", answers("n")):
                self.assertNotIn("kanban-app", inst.ask_components())
        buf = io.StringIO()
        with mock.patch.object(inst.sys, "platform", "linux"), contextlib.redirect_stdout(buf):
            with mock.patch("builtins.input", answers("y")):
                chosen = inst.ask_components()
        self.assertNotIn("kanban-app", chosen)
        self.assertIn("kanban", chosen)
        self.assertIn("macOS only", buf.getvalue())

    @ON_MAC
    def test_app_update_only_when_newer(self):
        kit = mini_kit(self.t)
        with fake.FakeRelease() as rel:
            rel.release("kanban-v0.3.1", self.zip_bytes("0.3.1"))
            res, out = self.install_app(kit, rel)
            self.assertEqual(res.returncode, 0, out)
            rel.log.clear()
            out = run(self.proj, "--update", "--yes", env=app_env(self.home, rel.base), kit=kit)
            self.assertIn("up to date", out)
            self.assertEqual(rel.log, [])  # same version: no request at all, so no asset request
            # a newer kit version whose release is not published yet: the fallback is the installed version
            set_version(kit, "0.3.2")
            rel.api(fake.releases_api())
            out = run(self.proj, "--update", "--yes", env=app_env(self.home, rel.base), kit=kit)
            self.assertIn("up to date", out)
            self.assertEqual(rel.asset_requests(), [])
            # once 0.3.2 is published it is installed
            rel.release("kanban-v0.3.2", self.zip_bytes("0.3.2"))
            out = run(self.proj, "--update", "--yes", env=app_env(self.home, rel.base), kit=kit)
            self.assertEqual(installed_version(self.home), "0.3.2")
            self.assertEqual(self.manifest()["kanban_app"]["version"], "0.3.2")
            # an installed app without a readable Info.plist counts as older
            (self.apps / "Kanban.app/Contents/Info.plist").write_text("garbage")
            rel.log.clear()
            run(self.proj, "--update", "--yes", env=app_env(self.home, rel.base), kit=kit)
            self.assertEqual(installed_version(self.home), "0.3.2")
            self.assertEqual(len(rel.asset_requests()), 1)
        # a project whose manifest does not list the app: --update never touches it
        other = self.t / "other"
        other.mkdir()
        run(other, "--only", "agents", "--yes", env=app_env(self.home))
        self.assertNotIn("Kanban desktop app", run(other, "--update", "--yes", env=app_env(self.home)))

    def test_app_from_source_plan(self):
        res = run_raw("--only", "kanban-app", "--from-source", "--dry-run", env=bare_env(self.home))
        out = res.stdout + res.stderr
        if MACOS:
            self.assertEqual(res.returncode, 0, out)
        self.assertIn("cargo tauri build --bundles app", out)
        self.assertIn("bundle/macos/Kanban.app", out)
        self.assertIn(str(self.apps / "Kanban.app"), out)
        self.assertIn("the build uses the system Python", out)
        self.assertFalse(self.apps.exists())
        out = run(self.proj, "--only", "kanban-app", "--from-source", "--dry-run", env=bare_env(self.home))
        self.assertIn("cargo tauri build --bundles app", out)
        self.assertFalse((self.proj / ".claude").exists())
        if MACOS:  # without cargo: the hints for the developer to run, nothing built, non-zero exit
            res = run_raw("--only", "kanban-app", "--from-source", env=bare_env(self.home))
            out = res.stdout + res.stderr
            self.assertNotEqual(res.returncode, 0, out)
            self.assertIn("https://sh.rustup.rs", out)
            self.assertIn("cargo install tauri-cli --version '^2' --locked", out)
            self.assertNotIn("Building", out)
            self.assertFalse(self.apps.exists())

    def test_python_licences_not_copied(self):
        run(self.proj, "--only", "agents", "--yes", env=bare_env(self.home))
        copied = sorted(p.name for p in (self.proj / ".claude/agentic-kit/LICENSES").iterdir())
        self.assertEqual(copied, sorted(p.name for p in (KIT / "LICENSES").iterdir() if p.is_file()))
        self.assertFalse([n for n in copied if "python" in n.lower() or n.lower().startswith(("psf", "cpython"))])

    def test_releases_lock_format(self):
        lock = json.loads((KIT / "plugins/kanban/app/releases.lock").read_text())
        self.assertIsInstance(lock, dict)
        for tag, assets in lock.items():
            if tag.startswith("_"):
                continue
            self.assertRegex(tag, r"^kanban-v\d+\.\d+\.\d+$")
            for name, digest in assets.items():
                self.assertIn(name, ("Kanban.app.zip", "Kanban.dmg"))
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
        inst = load_installer()
        self.assertIsInstance(inst.load_lock(KIT / "plugins/kanban/app/releases.lock"), dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
