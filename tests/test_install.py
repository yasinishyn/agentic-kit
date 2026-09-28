#!/usr/bin/env python3
"""Tests for install.py: selection, never-overwrite, skip-on-clash, settings merge, update, dry run.

Run: python3 tests/test_install.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent


APPROVE_TOOL = "mcp__plugin_kanban_kanban__kanban_approve"
TOKEN_DENY = "Read(~/Library/Application Support/Kanban/ui.token)"


def run_raw(*args, env=None):
    return subprocess.run([sys.executable, str(KIT / "install.py"), *[str(a) for a in args]],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, env=env)


def run(target, *args, env=None):
    res = run_raw(target, *args, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def bare_env(home: Path) -> dict:
    """No cargo, no claude: PATH has only the system folders and HOME has no ~/.cargo."""
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "LANG": "C.UTF-8"}


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

    # ---------------------------------------------------------------- kanban-app (PRD-08)
    def test_kanban_app_dry_run(self):
        home = self.t / "home"
        home.mkdir()
        res = run_raw("--only", "kanban-app", "--dry-run", env=bare_env(home))
        out = res.stdout + res.stderr
        self.assertEqual(res.returncode, 0, out)
        self.assertIn("cargo tauri build --bundles app", out)
        self.assertIn("bundle/macos/Kanban.app", out)
        self.assertIn(str(home / "Applications" / "Kanban.app"), out)
        self.assertFalse((home / "Applications").exists())  # dry run changes nothing
        # with a target folder too, and nothing is recorded
        out = run(self.t, "--only", "kanban-app", "--dry-run", env=bare_env(home))
        self.assertIn("cargo tauri build --bundles app", out)
        self.assertFalse((self.t / ".claude").exists())

    def test_kanban_app_not_in_all_or_update(self):
        home = self.t / "home"
        home.mkdir()
        proj = self.t / "proj"
        proj.mkdir()
        out = run(proj, "--all", "--yes", "--dry-run", env=bare_env(home))
        self.assertNotIn("cargo tauri", out)
        self.assertNotIn("kanban-app", out)
        run(proj, "--only", "agents", "--yes", env=bare_env(home))
        manifest_path = proj / ".claude/agentic-kit/installed.json"
        run(proj, "--only", "agents,kanban-app", "--dry-run", env=bare_env(home))
        manifest = json.loads(manifest_path.read_text())
        # even a manifest that somehow lists it never makes --update build the app
        manifest["components"] = ["agents", "kanban-app"]
        manifest_path.write_text(json.dumps(manifest))
        out = run(proj, "--update", "--yes", env=bare_env(home))
        self.assertNotIn("cargo tauri", out)
        self.assertNotIn("kanban-app", json.loads(manifest_path.read_text())["components"])
        res = run_raw(proj, "--only", "agents,kanban-app", env=bare_env(home))  # fails: no cargo
        self.assertNotIn("kanban-app", json.loads(manifest_path.read_text())["components"])
        self.assertNotEqual(res.returncode, 0)
        list_out = run_raw("--list").stdout
        self.assertIn("kanban-app", list_out)
        self.assertIn("kanban-app", run_raw("--help").stdout)

    def test_kanban_app_missing_prereqs(self):
        home = self.t / "home"
        home.mkdir()
        res = run_raw("--only", "kanban-app", env=bare_env(home))
        out = res.stdout + res.stderr
        self.assertNotEqual(res.returncode, 0, out)
        self.assertIn("https://sh.rustup.rs", out)
        self.assertIn("cargo install tauri-cli --version '^2' --locked", out)
        self.assertNotIn("Building", out)
        self.assertFalse((home / "Applications").exists())

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
