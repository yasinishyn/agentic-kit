#!/usr/bin/env python3
"""Regression test for the QA abuse checklist (fixtures/abuse_probe.py): every probe must pass.

Run: python3 plugins/kanban/tests/test_abuse.py
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

PROBE = Path(__file__).resolve().parent / "fixtures" / "abuse_probe.py"


class AbuseChecklistTests(unittest.TestCase):
    def test_every_abuse_probe_passes(self):
        env = {k: v for k, v in os.environ.items() if k not in ("KANBAN_HOME", "CLAUDE_PROJECT_DIR")}
        out = subprocess.run([sys.executable, str(PROBE)], capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(out.returncode, 0, out.stdout[-3000:] + out.stderr[-2000:])
        self.assertIn("passed", out.stdout.splitlines()[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
