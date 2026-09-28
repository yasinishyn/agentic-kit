#!/usr/bin/env python3
"""Static checks for the kanban plugin skills and the kit `sdd` amendment (stdlib only).

Run: python3 plugins/kanban/tests/test_skills.py
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
SKILLS = PLUGIN / "skills"
KIT_SDD = REPO / ".claude" / "skills" / "sdd"
KIT_FILES = (KIT_SDD / "SKILL.md", KIT_SDD / "reference" / "stages.md")

# Architecture §3.3 (kanban-v0-3-agent-board), literal.
CONTRACT = {
    "kanban_board", "kanban_new_ticket", "kanban_move", "kanban_add_subtask", "kanban_set_status",
    "kanban_check", "kanban_start", "kanban_heartbeat", "kanban_finish", "kanban_approval", "kanban_approve",
}
TEMPLATES = ("ticket.md", "discovery.md", "open-questions.md", "architecture.md", "adr.md", "prd.md")

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
TEMPLATE_PATH = re.compile(r"(?<![\w./-])((?:skills/sdd/)?templates/[\w.-]+\.md)")
TOOL = re.compile(r"\bkanban_[a-z_]*[a-z]")


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    fields = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            fields[key.strip()] = value.strip()
    return fields


def skill_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


class SkillTests(unittest.TestCase):
    def test_skill_frontmatter(self):
        names = set()
        for path in skill_files():
            fields = frontmatter(path.read_text())
            self.assertTrue(fields.get("name"), path)
            self.assertTrue(fields.get("description"), path)
            self.assertEqual(fields["name"], path.parent.name, path)
            names.add(fields["name"])
        self.assertEqual(names, {"sdd", "ticket", "kanban"})
        sdd = frontmatter((SKILLS / "sdd" / "SKILL.md").read_text())
        self.assertEqual(sdd.get("argument-hint"), "<issue or ticket slug>")
        for trigger in ("spec this", "new ticket", "let's build", "/kanban:sdd", "hand-off"):
            self.assertIn(trigger, sdd["description"])

    def test_skill_links_resolve(self):
        for path in skill_files():
            text = path.read_text()
            for target in LINK.findall(text):
                if target.startswith(("http:", "https:", "#", "mailto:")) or "<" in target:
                    continue
                resolved = (path.parent / target.split("#")[0]).resolve()
                self.assertTrue(resolved.exists(), f"{path}: broken link {target}")
            for ref in TEMPLATE_PATH.findall(text):
                base = PLUGIN if ref.startswith("skills/") else path.parent
                self.assertTrue((base / ref).exists(), f"{path}: missing {ref}")
        for name in TEMPLATES:
            self.assertTrue((SKILLS / "sdd" / "templates" / name).is_file(), name)

    def test_tool_names_match_contract(self):
        for path in [*skill_files(), *KIT_FILES]:
            found = set(TOOL.findall(path.read_text()))
            self.assertLessEqual(found, CONTRACT, f"{path}: {sorted(found - CONTRACT)}")
        kanban = set(TOOL.findall((SKILLS / "kanban" / "SKILL.md").read_text()))
        self.assertEqual(kanban, CONTRACT)

    def test_approval_rules_present(self):
        kanban = " ".join((SKILLS / "kanban" / "SKILL.md").read_text().split()).lower()
        self.assertIn("a channel event never approves", kanban)
        self.assertIn("user's own chat message", kanban)
        self.assertIn("never add `kanban_approve` to auto-allowed", kanban)
        for status in ("already_claimed", "superseded"):
            self.assertIn(status, kanban)
        for path in KIT_FILES:
            text = path.read_text()
            self.assertTrue("kanban_approval" in text, path)
            self.assertTrue("board_recorded" in text, path)
        sdd = " ".join((SKILLS / "sdd" / "SKILL.md").read_text().split())
        for needle in ("kanban_approval", "board_recorded=true", "needs_input", "OPEN-QUESTIONS.md", "STOP"):
            self.assertIn(needle, sdd)

    def test_ticket_is_alias(self):
        text = (SKILLS / "ticket" / "SKILL.md").read_text()
        self.assertIn("kanban:sdd", frontmatter(text)["description"])
        self.assertLess(len(text.splitlines()), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
