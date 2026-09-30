#!/usr/bin/env python3
"""End-to-end tests for the markdown kanban (stdlib only): store, MCP protocol, web board and viewer.

Run: python3 plugins/kanban/tests/test_kanban.py
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

import helpers  # noqa: E402  (temp KANBAN_HOME, KANBAN_NO_DAEMON=1 for this process and its children)
import kanban_md as km  # noqa: E402

SCRIPTS = helpers.SCRIPTS

PRD = """---
title: PRD-01 Login form
status: doing
---

# PRD-01 Login form

## Acceptance criteria
- [x] shows an error summary
- [ ] locks after 5 attempts

```
- [ ] not a checkbox (code fence)
```
"""


CONCURRENT = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import kanban_md as km
root, role, start, n = Path(sys.argv[2]), sys.argv[3], float(sys.argv[4]), int(sys.argv[5])
while time.time() < start:
    pass
for i in range(n):
    if role == "mover":  # the MCP kanban_move / daemon h_move path
        km.move_ticket(root, "login", ("qa", "developer")[i % 2])
    else:  # the approval writer (README approved_* + the 03 line)
        km.approve(root, "login", "human (chat)")
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".git").mkdir()
        self.env = {**os.environ, "KANBAN_PROJECT_DIR": str(self.root)}
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, rel):
        return (self.root / rel).read_text()


class StoreTests(Base):
    def test_ticket_lifecycle_in_markdown(self):
        t = km.create_ticket(self.root, "Password reset page", summary="Users can reset a forgotten password.")
        self.assertEqual(t["id"], "password-reset-page")
        readme = ".SDD/specs/password-reset-page/README.md"
        self.assertIn("status: discovery", self.read(readme))
        self.assertEqual(t["progress"], {"done": 0, "total": 7})
        with self.assertRaises(ValueError):
            km.create_ticket(self.root, "Password reset page")  # never overwrites
        km.approve(self.root, "password-reset-page", "human (chat)")  # entering Developer needs an approval
        km.move_ticket(self.root, "password-reset-page", "developer")
        text = self.read(readme)
        self.assertIn("status: developer", text)
        self.assertIn("- [x] Discovery", text)
        self.assertIn("- [x] Approval", text)
        self.assertIn("- [ ] Developer", text)
        with self.assertRaises(ValueError):
            km.move_ticket(self.root, "password-reset-page", "shipped")

    def test_concurrent_writers_never_lose_an_update(self):
        """B6: README writers in different processes serialise on the ticket lock (no lost read-modify-write)."""
        km.create_ticket(self.root, "Login")
        folder = self.root / ".SDD/specs/login"
        (folder / "03-architecture.md").write_text("# Architecture\n\n| a | b |\n|---|---|\n\nBody.\n")
        fresh = km.set_fields((folder / "README.md").read_text(), {"status": "developer"})  # pre-v0.3: in EXECUTION
        moves = 30  # even: the mover's last write is "developer"
        for iteration in range(20):
            (folder / "README.md").write_text(fresh)
            start = time.time() + 0.3
            procs = [subprocess.Popen([sys.executable, "-c", CONCURRENT, str(SCRIPTS), str(self.root), role,
                                       str(start), str(moves)], env=self.env, stderr=subprocess.PIPE, text=True)
                     for role in ("mover", "approver")]
            for proc in procs:
                _, err = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 0, err)
            text = (folder / "README.md").read_text()
            fields = km.split_frontmatter(text)[0]
            self.assertEqual(fields.get("status"), "developer", f"iteration {iteration}: a stale write won")
            self.assertEqual(fields.get("approved_by"), "human (chat)", f"iteration {iteration}: approval lost")
            for stage in ("Discovery", "Architect", "Approval", "Developer"):
                self.assertIn(f"- [x] {stage}", text, f"iteration {iteration}")
            self.assertIn("- [ ] QA", text)

    def test_ticket_lock_blocks_other_processes(self):
        """B6: while one process holds km.ticket_lock, another process's move waits for it."""
        km.create_ticket(self.root, "Login")
        folder = self.root / ".SDD/specs/login"
        code = ("import sys, time\nsys.path.insert(0, sys.argv[1])\nfrom pathlib import Path\nimport kanban_md as km\n"
                "km.move_ticket(Path(sys.argv[2]), 'login', 'architect')\nprint(time.time())\n")
        with km.ticket_lock(folder):
            with km.ticket_lock(folder):  # re-entrant in the holding thread (daemon h_move → km.move_ticket)
                pass
            held = time.time()
            proc = subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(self.root)], env=self.env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(1.0)
            self.assertIsNone(proc.poll(), "the move did not wait for the lock")
            self.assertIn("status: discovery", (folder / "README.md").read_text())
            released = time.time()
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        self.assertGreaterEqual(float(out.strip()), released)
        self.assertGreater(released - held, 0.9)
        self.assertIn("status: architect", (folder / "README.md").read_text())

    def test_subtasks_prd_status_and_checkboxes(self):
        km.create_ticket(self.root, "Login")
        (self.root / ".SDD/specs/login/prd").mkdir()
        (self.root / ".SDD/specs/login/prd/PRD-01-login-form.md").write_text(PRD)
        rel = km.add_subtask(self.root, "login", "Write the demo script", checklist=["record", "screenshots"])
        self.assertEqual(rel, ".SDD/specs/login/tasks/01-write-the-demo-script.md")
        t = km.list_tickets(self.root)[0]
        prd = next(s for s in t["subtasks"] if s["path"].endswith("PRD-01-login-form.md"))
        self.assertEqual((prd["status"], prd["done"], prd["total"]), ("doing", 1, 2))  # fenced box ignored
        km.set_status(self.root, ".SDD/specs/login/prd/PRD-01-login-form.md", "done")
        self.assertIn("status: done", self.read(".SDD/specs/login/prd/PRD-01-login-form.md"))
        self.assertIn("title: PRD-01 Login form", self.read(".SDD/specs/login/prd/PRD-01-login-form.md"))
        km.check(self.root, rel, "screen")
        self.assertIn("- [x] screenshots", self.read(rel))
        km.check(self.root, rel, 1)
        km.check(self.root, rel, 1, done=False)
        self.assertIn("- [ ] record", self.read(rel))
        with self.assertRaises(ValueError):
            km.set_status(self.root, rel, "developer")  # sub-tasks use todo/doing/blocked/done
        with self.assertRaises(ValueError):
            km.check(self.root, "../outside.md", 1)  # confined to .SDD/specs

    def test_existing_spec_without_frontmatter_and_ignored_folders(self):
        spec = self.root / ".SDD/specs/legacy-spec"
        spec.mkdir(parents=True)
        (spec / "README.md").write_text("# Legacy spec\n\nNo frontmatter yet.\n")
        (self.root / ".SDD/specs/_validation").mkdir()
        tickets = km.list_tickets(self.root)
        self.assertEqual([t["id"] for t in tickets], ["legacy-spec"])
        self.assertEqual((tickets[0]["status"], tickets[0]["status_set"], tickets[0]["title"]),
                         ("discovery", False, "Legacy spec"))
        km.move_ticket(self.root, "legacy-spec", "approval")  # adds frontmatter, keeps the body
        text = (spec / "README.md").read_text()
        self.assertTrue(text.startswith("---\nstatus: approval\n"))
        self.assertIn("No frontmatter yet.", text)


class McpAndUiTests(Base):
    def setUp(self):
        super().setUp()
        self.env["KANBAN_PORT"] = str(helpers.free_port())
        self.proc = subprocess.Popen([sys.executable, str(SCRIPTS / "server.py")], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env,
                                     cwd=str(self.root))
        self.n = 0

    def tearDown(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()
        super().tearDown()

    def rpc(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return None if notify else json.loads(self.proc.stdout.readline())

    def call(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return res["content"][0]["text"], res["isError"]

    def http(self, method, path, body=None, headers=None):
        for _ in range(50):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", int(self.env["KANBAN_PORT"]), timeout=5)
                conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
                resp = conn.getresponse()
                return resp.status, resp.read()
            except ConnectionRefusedError:
                time.sleep(0.1)
        self.fail("web board did not start")

    def test_mcp_protocol_and_tools(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
        self.assertEqual((init["protocolVersion"], init["serverInfo"]["name"]), ("2025-06-18", "kanban"))
        self.rpc("notifications/initialized", notify=True)
        self.assertEqual(self.rpc("ping")["result"], {})
        names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["kanban_board", "kanban_new_ticket", "kanban_move", "kanban_add_subtask",
                                 "kanban_set_status", "kanban_check", "kanban_start", "kanban_heartbeat",
                                 "kanban_finish", "kanban_approval", "kanban_approve"])
        text, err = self.call("kanban_new_ticket", title="Nightly import speed-up", summary="Make it < 10 min.")
        self.assertFalse(err, text)
        self.assertIn(".SDD/specs/nightly-import-speed-up/README.md", text)
        text, _ = self.call("kanban_add_subtask", ticket="nightly-import-speed-up", title="Profile the job",
                            checklist=["run profiler", "write findings"])
        self.call("kanban_check", path=".SDD/specs/nightly-import-speed-up/tasks/01-profile-the-job.md", item=1)
        self.call("kanban_move", ticket="nightly-import-speed-up", stage="architect")
        text, _ = self.call("kanban_board")
        self.assertIn("## Architect (1)", text)
        self.assertIn("todo: Profile the job [1/2]", text)
        self.assertIn("http://127.0.0.1:", text)
        text, err = self.call("kanban_move", ticket="nope", stage="qa")
        self.assertTrue(err)
        self.assertEqual(self.rpc("bogus")["error"]["code"], -32601)

    def test_web_board_viewer_and_guards(self):
        self.rpc("initialize", {"protocolVersion": "2024-11-05"})
        self.call("kanban_new_ticket", title="Login")
        (self.root / ".SDD/specs/login/prd").mkdir()
        (self.root / ".SDD/specs/login/prd/PRD-01-login-form.md").write_text(PRD + "\nSee [the ticket](../README.md).\n")
        status, body = self.http("GET", "/")
        self.assertEqual(status, 200)
        board = json.loads(self.http("GET", "/api/board")[1])
        self.assertEqual(board["tickets"][0]["subtasks"][0]["done"], 1)
        self.assertIn(".SDD/specs/login/prd/PRD-01-login-form.md", board["tickets"][0]["files"])
        q = urllib.parse.quote(".SDD/specs/login/prd/PRD-01-login-form.md")
        status, page = self.http("GET", f"/view?path={q}")
        self.assertEqual(status, 200)
        self.assertIn(b"<input type=checkbox disabled checked>", page)
        self.assertIn(b"/view?path=.SDD/specs/login/README.md", page)  # relative md link rewritten
        self.assertIn(b"vscode://file/", page)
        status, _ = self.http("GET", "/view?path=" + urllib.parse.quote("../../etc/passwd"))
        self.assertEqual(status, 404)  # outside .SDD/specs
        status, _ = self.http("GET", "/api/board", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.http("POST", "/api/move", {"ticket": "login", "stage": "qa"})
        self.assertEqual(status, 403)  # no X-Kanban header
        status, _ = self.http("POST", "/api/move", {"ticket": "login", "stage": "architect"}, {"X-Kanban": "1"})
        self.assertEqual(status, 200)  # qa would need an approval first
        self.assertIn("status: architect", self.read(".SDD/specs/login/README.md"))
        status, _ = self.http("POST", "/api/status", {"path": ".SDD/specs/login/prd/PRD-01-login-form.md",
                                                      "status": "blocked"}, {"X-Kanban": "1"})
        self.assertEqual(status, 200)
        self.assertIn("status: blocked", self.read(".SDD/specs/login/prd/PRD-01-login-form.md"))
        status, _ = self.http("POST", "/api/new", {"title": "From the board"}, {"X-Kanban": "1"})
        self.assertEqual(status, 200)
        self.assertTrue((self.root / ".SDD/specs/from-the-board/README.md").exists())
        self.assertEqual((self.root / ".kanban/.gitignore").read_text(), "*\n")


class McpModeTests(Base):
    """server.py against the board daemon, in a temp KANBAN_HOME (never the real one)."""

    def setUp(self):
        super().setUp()
        self.home = helpers.temp_home()
        self.env.update({"KANBAN_HOME": str(self.home), "KANBAN_PORT": str(helpers.free_port())})
        self.old = None

    def tearDown(self):
        if self.old:
            self.old.stop()
        helpers.kill_home_daemon(self.home)
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)
        super().tearDown()

    def run_server(self, *calls):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}]
        msgs += [{"jsonrpc": "2.0", "id": i + 2, "method": "tools/call", "params": {"name": n, "arguments": a}}
                 for i, (n, a) in enumerate(calls)]
        out = subprocess.run([sys.executable, str(SCRIPTS / "server.py")], input="".join(json.dumps(m) + "\n"
                             for m in msgs), capture_output=True, text=True, env=self.env, cwd=str(self.root),
                             timeout=60)
        replies = [json.loads(line) for line in out.stdout.splitlines()]
        return [r["result"]["content"][0]["text"] for r in replies[1:]], out.stderr

    def test_mcp_local_mode(self):
        self.env["KANBAN_NO_DAEMON"] = "1"
        (text,), err = self.run_server(("kanban_board", {}))
        self.assertEqual(err.count("kanban: local mode"), 1, err)
        self.assertIn("KANBAN_NO_DAEMON=1", err)
        self.assertIn(f"Board: http://127.0.0.1:{self.env['KANBAN_PORT']}/", text)
        self.assertTrue((self.root / ".kanban/url").exists())  # v0.2 embedded board
        self.assertFalse((self.home / "daemon.json").exists())

    def test_board_url_has_no_token(self):
        self.env.pop("KANBAN_NO_DAEMON", None)
        (created, text), err = self.run_server(("kanban_new_ticket", {"title": "Login"}), ("kanban_board", {}))
        self.assertNotIn("local mode", err)
        info = helpers.read_json(self.home / "daemon.json")
        # the plugin may run only on the app's bundled Python: the hint goes through kpython, never bare python3
        self.assertIn(f"Board: http://127.0.0.1:{info['port']}/ (open it with: sh ", text)
        self.assertIn("scripts/kpython ", text)
        self.assertIn("daemon.py --open)", text)
        self.assertNotIn("python3 ", text.split("Board:")[1])
        for name in ("client.token", "ui.token"):
            self.assertNotIn((self.home / name).read_text().strip(), text + created + err)
        self.assertFalse((self.root / ".kanban").exists())  # no embedded board in daemon mode
        token = (self.home / "client.token").read_text().strip()
        status, _, body = helpers.request(info["port"], "GET", "/api/projects", token=token)
        self.assertEqual([p["root"] for p in json.loads(body)["projects"]], [str(self.root.resolve())])

    def test_unresolved_api_mismatch_falls_back_to_local_mode(self):
        self.old = helpers.TestDaemon(home=self.home, KANBAN_DAEMON_API="2", KANBAN_DAEMON_VERSION="9.0.0").start()
        self.env.pop("KANBAN_NO_DAEMON", None)
        (text,), err = self.run_server(("kanban_board", {}))
        self.assertEqual(err.count("kanban: local mode"), 1, err)
        self.assertIn("api 2", err)
        self.assertIn(f"Board: http://127.0.0.1:{self.env['KANBAN_PORT']}/", text)
        self.assertIsNone(self.old.proc.poll())  # the newer daemon is left alone


if __name__ == "__main__":
    unittest.main(verbosity=2)
