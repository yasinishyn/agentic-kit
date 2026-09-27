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


def run(target, *args):
    res = subprocess.run([sys.executable, str(KIT / "install.py"), str(target), *args],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
