#!/usr/bin/env python3
"""PRD-05 spec endpoints (routes_specs.py): file GET/PUT confined to .SDD/specs/**.md with If-Match and protected
frontmatter keys, the diff since the approval snapshot, approval badges, the approve-then-move flow and the read-only
git panel (exact argv). Every daemon runs in a temp KANBAN_HOME (stdlib only)."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.parse
from pathlib import Path

import helpers
import kanban_md as km
import routes_specs
from test_daemon import Stream

ARCH = "---\ntitle: Architecture\n---\n\n# Architecture\n\nThe board keeps markdown.\n"
PRD = "---\ntitle: PRD-01\nstatus: todo\n---\n\n# PRD-01\n\n- [ ] one\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class SpecsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kanban-specs-"))
        self.root = helpers.make_project(self.tmp)
        km.create_ticket(self.root, "Login")
        self.folder = km.specs_dir(self.root) / "login"
        (self.folder / "03-architecture.md").write_text(ARCH)
        (self.folder / "prd").mkdir()
        (self.folder / "prd" / "PRD-01-core.md").write_text(PRD)
        self.d = helpers.TestDaemon().start()
        self.pid = self.d.register(self.root)["project_id"]
        self.base = f"/api/projects/{self.pid}"

    def tearDown(self):
        self.d.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers
    def file_url(self, rel: str) -> str:
        return f"{self.base}/files?path={urllib.parse.quote(rel)}"

    def get_file(self, rel: str, token="client"):
        return self.d.call("GET", self.file_url(rel), token=token)

    def put_file(self, rel: str, content: str, if_match: str | None, token="ui"):
        headers = {"If-Match": if_match} if if_match is not None else None
        return self.d.call("PUT", self.file_url(rel), {"content": content}, token=token, headers=headers)

    def board_ticket(self, ticket="login") -> dict:
        status, board = self.d.get(f"{self.base}/board")
        self.assertEqual(status, 200, board)
        return next(t for t in board["tickets"] if t["id"] == ticket)

    # ---- tests
    def test_plugin_loaded(self):
        status, health = self.d.get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("routes_specs", health["plugins"]["loaded"], self.d.log_text())

    def test_get_file(self):
        rel = ".SDD/specs/login/03-architecture.md"
        status, body = self.get_file(rel)
        self.assertEqual(status, 200, body)
        self.assertEqual((body["path"], body["content"], body["sha256"]), (rel, ARCH, sha(ARCH)))
        self.assertEqual(body["ticket"], "login")

    def test_file_traversal_refused(self):
        outside = self.tmp / "outside.md"
        outside.write_text("# secret\n")
        (self.root / "notes.md").write_text("# not a spec\n")
        (self.folder / "tool.py").write_text("print('x')\n")
        (self.folder / "link.md").symlink_to(outside)
        (self.folder / "dirlink").symlink_to(self.tmp, target_is_directory=True)
        bad = ["../outside.md", ".SDD/specs/../../outside.md", ".SDD/specs/login/../../../outside.md",
               str(outside), "/etc/hosts", "notes.md", ".SDD/specs/login/tool.py", ".SDD/specs/login/link.md",
               ".SDD/specs/login/dirlink/outside.md", ".SDD/specs/login", ".SDD/specs/login/missing.md", ""]
        for rel in bad:
            status, body = self.get_file(rel)
            self.assertTrue(400 <= status < 500, (rel, status, body))
            self.assertNotIn("secret", str(body))
            status, body = self.put_file(rel, "# pwned\n", sha("# secret\n"))
            self.assertTrue(400 <= status < 500, (rel, status, body))
        self.assertEqual(outside.read_text(), "# secret\n")
        self.assertEqual((self.folder / "tool.py").read_text(), "print('x')\n")
        self.assertEqual(self.d.call("GET", f"{self.base}/files", token="client")[0], 400)

    def test_put_if_match_and_protected_keys(self):
        rel = ".SDD/specs/login/README.md"
        readme = self.root / rel
        before = readme.read_text()
        edited = before.replace("## Progress", "Some context.\n\n## Progress")
        # no If-Match → 428; stale → 409
        self.assertEqual(self.put_file(rel, edited, None)[0], 428)
        status, body = self.put_file(rel, edited, sha("stale"))
        self.assertEqual(status, 409, body)
        self.assertEqual(body["reason"], "stale")
        self.assertEqual(readme.read_text(), before)
        # protected frontmatter keys
        for changed in (km.set_fields(edited, {"status": "developer"}),
                        km.set_fields(edited, {"approved_by": "human (board)"}),
                        km.set_fields(edited, {"approved_at": "2026-01-01"}),
                        km.set_fields(edited, {"approved_hash": "a" * 64}),
                        edited.replace("status: discovery\n", ""),
                        edited.replace("status: discovery\n", "status: discovery\nstatus: developer\n")):
            status, body = self.put_file(rel, changed, sha(before))
            self.assertEqual(status, 409, (changed, body))
            self.assertEqual(body["reason"], "protected")
            self.assertIn("move/approve", body["error"])
            self.assertEqual(readme.read_text(), before)
        # client token cannot write
        status, body = self.put_file(rel, edited, sha(before), token="client")
        self.assertEqual(status, 403, body)
        self.assertEqual(readme.read_text(), before)
        # a valid edit: atomic write, new hash, board.changed
        stream = Stream(self.d.port, self.d.ui_token, project=self.pid)
        try:
            self.assertEqual(stream.next("hello")["event"], "hello")
            status, body = self.put_file(rel, edited, f'"{sha(before)}"')
            self.assertEqual(status, 200, body)
            self.assertEqual((body["path"], body["sha256"]), (rel, sha(edited)))
            ev = stream.next("board.changed")
            self.assertIsNotNone(ev)
            self.assertEqual(ev["data"].get("ticket"), "login")
        finally:
            stream.close()
        self.assertEqual(readme.read_text(), edited)
        self.assertEqual([p.name for p in self.folder.iterdir() if p.name.endswith(".tmp")], [])
        # a sub-task's status is protected too; its other keys and body are not
        prd = ".SDD/specs/login/prd/PRD-01-core.md"
        status, body = self.put_file(prd, PRD.replace("status: todo", "status: done"), sha(PRD))
        self.assertEqual(status, 409, body)
        status, body = self.put_file(prd, PRD.replace("title: PRD-01", "title: PRD-01 core") + "- [ ] two\n",
                                     sha(PRD))
        self.assertEqual(status, 200, body)
        # a non-JSON or content-less body is refused
        status, body = self.d.call("PUT", self.file_url(prd), {"text": "x"}, token="ui",
                                   headers={"If-Match": body["sha256"]})
        self.assertEqual(status, 400, body)

    def test_approve_snapshot_diff_and_badges(self):
        km.move_ticket(self.root, "login", "approval")
        status, diff = self.d.get(f"{self.base}/diff/login")
        self.assertEqual(status, 200, diff)
        self.assertIsNone(diff["approval"])
        self.assertIn("03-architecture.md", diff["files"])
        self.assertEqual(diff["hash"], km.spec_hash(self.folder))
        status, body = self.d.call("POST", f"{self.base}/tickets/login/approve", {}, token="ui")
        self.assertEqual(status, 200, body)
        t = self.board_ticket()
        self.assertEqual((t["approval"]["state"], t["approval"]["recorded"], t["approval"]["board_recorded"]),
                         ("valid", True, True))
        status, diff = self.d.get(f"{self.base}/diff/login")
        self.assertEqual((status, diff["changed"], diff["diff"]), (200, False, ""))
        self.assertEqual(diff["approval"]["actor"], "human (board)")
        # edit after approval (through the editor) → diff line + changed state
        rel = ".SDD/specs/login/03-architecture.md"
        current = (self.root / rel).read_text()
        status, body = self.put_file(rel, current + "\nA new constraint.\n", sha(current))
        self.assertEqual(status, 200, body)
        status, diff = self.d.get(f"{self.base}/diff/login")
        self.assertEqual(status, 200, diff)
        self.assertTrue(diff["changed"])
        self.assertIn("+A new constraint.", diff["diff"].splitlines())
        self.assertIn("--- a/03-architecture.md", diff["diff"])
        self.assertEqual(diff["changed_files"], ["03-architecture.md"])
        self.assertEqual(self.board_ticket()["approval"]["state"], "changed")
        # hand-edited approved_* without a matching approvals row → not recorded
        readme = self.folder / "README.md"
        km.write_atomic(readme, km.set_fields(readme.read_text(), {"approved_hash": km.spec_hash(self.folder)}))
        t = self.board_ticket()
        self.assertEqual((t["approval"]["state"], t["approval"]["recorded"], t["approval"]["board_recorded"]),
                         ("valid", False, False))
        # unknown ticket / bad id
        self.assertEqual(self.d.get(f"{self.base}/diff/nope")[0], 404)
        self.assertEqual(self.d.get(f"{self.base}/diff/..")[0] // 100, 4)

    def test_approve_then_move(self):
        km.move_ticket(self.root, "login", "approval")
        move = f"{self.base}/tickets/login/move"
        status, body = self.d.call("POST", move, {"stage": "developer"}, token="ui")
        self.assertEqual((status, body.get("reason")), (409, "approval_required"))
        self.assertEqual(self.board_ticket()["status"], "approval")
        self.assertEqual(self.d.call("POST", f"{self.base}/tickets/login/approve", {}, token="client")[0], 403)
        self.assertEqual(self.d.call("POST", f"{self.base}/tickets/login/approve", {}, token="ui")[0], 200)
        status, body = self.d.call("POST", move, {"stage": "developer"}, token="ui")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.board_ticket()["status"], "developer")

    def test_git_argv_exact(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            out = "main\n" if "rev-parse" in argv else "M  staged.py\n M changed.py\n?? new.md\nR  old.py -> new.py\n"
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

        result = routes_specs.git_status(self.root, run=fake_run)
        root = str(self.root)
        self.assertEqual([c[0] for c in calls], [
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=", "-C", root, "status", "--porcelain=v1"],
            ["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
        ])
        for argv, kwargs in calls:
            self.assertIsInstance(argv, list)
            self.assertFalse(kwargs.get("shell"))
            self.assertTrue(0 < kwargs.get("timeout", 0) <= 10)
        self.assertEqual(result["branch"], "main")
        self.assertEqual(result["staged"], ["staged.py", "new.py"])
        self.assertEqual(result["changed"], ["changed.py"])
        self.assertEqual(result["untracked"], ["new.md"])
        self.assertIsNone(result["error"])

        def broken(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 5)
        self.assertIn("git", routes_specs.git_status(self.root, run=broken)["error"])

    def test_git_endpoint_and_handoff_block(self):
        (self.folder / "handoff-note.md").write_text(
            "# Login — Hand-off note\n\n## Git — for the developer\nReview, then run:\n```bash\n"
            "git -C /repo status --short\ngit -C /repo commit -m \"PRD-01: x\" -- a.py\n```\n\n## Deploy\nnone\n")
        status, body = self.d.get(f"{self.base}/git?ticket=login")
        self.assertEqual(status, 200, body)
        for key in ("branch", "staged", "changed", "untracked", "error", "handoff"):
            self.assertIn(key, body)
        self.assertEqual(body["handoff"]["path"], ".SDD/specs/login/handoff-note.md")
        self.assertEqual(body["handoff"]["git_block"],
                         "git -C /repo status --short\ngit -C /repo commit -m \"PRD-01: x\" -- a.py")
        status, body = self.d.get(f"{self.base}/git")  # project-wide: the latest note of any ticket
        self.assertEqual((status, body["handoff"]["path"]), (200, ".SDD/specs/login/handoff-note.md"))
        (self.folder / "handoff-note.md").unlink()
        self.assertIsNone(self.d.get(f"{self.base}/git?ticket=login")[1]["handoff"])
        self.assertEqual(self.d.get(f"{self.base}/git?ticket=../x")[0], 400)
        if shutil.which("git"):
            repo = self.tmp / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "trunk", str(repo)], check=True, capture_output=True)
            (repo / "a.txt").write_text("x\n")
            result = routes_specs.git_status(repo)
            self.assertEqual((result["error"], result["branch"], result["untracked"]), (None, "trunk", ["a.txt"]))
        self.assertEqual(self.d.get(f"{self.base}/git", token=None)[0], 401)

    def test_ui_module_served(self):
        for path in ("/ui/modules/specs.js", "/ui/modules/specs.css"):
            status, _, body = helpers.request(self.d.port, "GET", path)
            self.assertEqual(status, 200, path)
            self.assertNotIn(b"innerHTML", body)
        status, listing = self.d.call("GET", "/ui/modules", token=None)
        self.assertIn("/ui/modules/specs.js", listing["js"])

    def test_approval_dialog_never_appends_null(self):
        """B11: DOM append(null) renders the text "null": optional dialog parts must be filtered, not passed as null."""
        js = (helpers.SCRIPTS.parent / "ui" / "modules" / "specs.js").read_text()
        for m in re.finditer(r"\.append\(", js):
            depth, i = 1, m.end()
            while depth and i < len(js):
                depth += {"(": 1, ")": -1}.get(js[i], 0)
                i += 1
            call = js[m.start():i]
            self.assertIsNone(re.search(r":\s*null\s*[,)]", call), call)

    def test_no_native_confirm(self):
        """window.confirm() is a no-op in the macOS WKWebView (wry has no JS confirm panel): in-page modal only."""
        ui = helpers.SCRIPTS.parent / "ui"
        native = re.compile(r"(?<![\w.])(?:window\.)?confirm\(|window\.confirm\b")
        for js in sorted(ui.rglob("*.js")):
            if "vendor" in js.parts:
                continue
            for n, line in enumerate(js.read_text().splitlines(), 1):
                self.assertIsNone(native.search(line), f"{js.relative_to(ui)}:{n}: {line.strip()}")
        status, _, app = helpers.request(self.d.port, "GET", "/ui/app.js")
        self.assertEqual(status, 200)
        self.assertIn("function confirmDialog(message, okLabel)", app.decode())
        self.assertIn("confirm: confirmDialog,", app.decode())  # exposed as window.kanban.confirm
        for module in ("runs.js", "transcript.js", "specs.js"):
            self.assertIn("window.kanban.confirm(", (ui / "modules" / module).read_text(), module)


if __name__ == "__main__":
    unittest.main()
