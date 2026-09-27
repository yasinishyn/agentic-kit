#!/usr/bin/env python3
"""End-to-end tests for the kanban plugin (stdlib only): store, hooks, MCP protocol, web board.

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
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import kanban_store as ks  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".git").mkdir()
        self.env = {**os.environ, "KANBAN_PROJECT_DIR": str(self.root)}
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def tearDown(self):
        self.tmp.cleanup()

    def board(self):
        return json.loads((self.root / ".kanban" / "board.json").read_text())


class StoreTests(Base):
    def test_add_move_update_delete_and_self_ignoring_folder(self):
        with ks.board(self.root) as data:
            feat = ks.add_card(data, "SDD: demo", "doing")
            prd = ks.add_card(data, "PRD-01: thing", parent=feat["id"])
            ks.update_card(data, feat["id"], add_step="Discovery")
            ks.update_card(data, feat["id"], complete_step=1)
            ks.move_card(data, prd["id"], "review")
        self.assertEqual((self.root / ".kanban" / ".gitignore").read_text(), "*\n")
        cards = {c["id"]: c for c in self.board()["cards"]}
        self.assertEqual(cards["K-1"]["steps"], [{"text": "Discovery", "done": True}])
        self.assertEqual(cards["K-2"]["status"], "review")
        with ks.board(self.root) as data:
            ks.delete_card(data, "K-1")  # deletes children too
        self.assertEqual(self.board()["cards"], [])

    def test_validation(self):
        with ks.board(self.root) as data:
            with self.assertRaises(ValueError):
                ks.add_card(data, "x", "nope")
            with self.assertRaises(KeyError):
                ks.move_card(data, "K-99", "done")
            card = ks.add_card(data, "line one\nline two " + "y" * 300)
            self.assertEqual(card["title"][:8], "line one")
            self.assertLessEqual(len(card["title"]), ks.MAX_TITLE)
            with self.assertRaises(ValueError):
                ks.update_card(data, card["id"], complete_step=1)


class HookTests(Base):
    def run_hook(self, mode, event):
        return subprocess.run([sys.executable, str(SCRIPTS / "hook.py"), mode], input=json.dumps(event),
                              capture_output=True, text=True, env=self.env, timeout=20)

    def test_prompt_creates_card_and_stop_moves_it_to_review(self):
        out = self.run_hook("prompt", {"session_id": "s1", "cwd": str(self.root), "prompt": "Fix the login bug\nmore"})
        self.assertEqual(out.returncode, 0, out.stderr)
        ctx = json.loads(out.stdout)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "UserPromptSubmit")
        self.assertIn("K-1", ctx["additionalContext"])
        self.assertEqual(self.board()["cards"][0]["status"], "doing")
        self.assertEqual(self.board()["cards"][0]["title"], "Fix the login bug")
        out = self.run_hook("stop", {"session_id": "s1", "cwd": str(self.root), "stop_hook_active": False})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["cards"][0]["status"], "review")

    def test_stop_keeps_done_cards_and_other_sessions(self):
        self.run_hook("prompt", {"session_id": "a", "prompt": "one"})
        self.run_hook("prompt", {"session_id": "b", "prompt": "two"})
        with ks.board(self.root) as data:
            ks.move_card(data, "K-1", "done")
        self.run_hook("stop", {"session_id": "a"})
        statuses = [c["status"] for c in self.board()["cards"]]
        self.assertEqual(statuses, ["done", "doing"])

    def test_never_blocks_on_bad_input(self):
        out = subprocess.run([sys.executable, str(SCRIPTS / "hook.py"), "prompt"], input="not json",
                             capture_output=True, text=True, env=self.env, timeout=20)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout, "")
        out = self.run_hook("prompt", {"session_id": "s", "prompt": "   "})
        self.assertEqual((out.returncode, out.stdout), (0, ""))


class McpAndUiTests(Base):
    def setUp(self):
        super().setUp()
        self.env["KANBAN_PORT"] = str(18700 + os.getpid() % 500)
        self.proc = subprocess.Popen([sys.executable, str(SCRIPTS / "server.py")], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env,
                                     cwd=str(self.root))
        self.next_id = 0

    def tearDown(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()
        super().tearDown()

    def rpc(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return None if notify else json.loads(self.proc.stdout.readline())

    def call(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return res["content"][0]["text"], res["isError"]

    def http(self, method, path, body=None, headers=None):
        port = int(self.env["KANBAN_PORT"])
        for _ in range(50):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request(method, path, body=json.dumps(body) if body is not None else None,
                             headers=headers or {})
                resp = conn.getresponse()
                return resp.status, resp.read()
            except ConnectionRefusedError:
                time.sleep(0.1)
        self.fail("web board did not start")

    def test_protocol_handshake_and_tools(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "0"}})["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["capabilities"])
        self.rpc("notifications/initialized", notify=True)
        self.assertEqual(self.rpc("ping")["result"], {})
        names = [t["name"] for t in self.rpc("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["kanban_list", "kanban_add", "kanban_move", "kanban_update", "kanban_delete"])
        text, err = self.call("kanban_add", title="SDD: demo", status="doing")
        self.assertFalse(err)
        self.assertIn("K-1", text)
        self.call("kanban_add", title="PRD-01", parent_id="K-1")
        self.call("kanban_update", card_id="K-1", add_step="Discovery")
        text, _ = self.call("kanban_update", card_id="K-1", complete_step=1)
        self.assertIn("1/1", text)
        self.call("kanban_move", card_id="K-2", status="review")
        text, _ = self.call("kanban_list")
        self.assertIn("## Review (1)", text)
        self.assertIn("http://127.0.0.1:", text)
        text, err = self.call("kanban_move", card_id="K-99", status="done")
        self.assertTrue(err)
        self.assertIn("no card K-99", text)
        self.assertEqual(self.rpc("nope/method")["error"]["code"], -32601)
        old = self.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"]
        self.assertEqual(old, "2025-06-18")

    def test_web_board_and_its_guards(self):
        self.rpc("initialize", {"protocolVersion": "2024-11-05"})
        self.call("kanban_add", title="Card from Claude")
        status, body = self.http("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Kanban</title>", body)
        status, body = self.http("GET", "/api/board")
        self.assertEqual(json.loads(body)["cards"][0]["title"], "Card from Claude")
        # no custom header -> cross-site style POST is refused
        status, _ = self.http("POST", "/api/move", {"card_id": "K-1", "status": "done"},
                              {"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        # foreign Host header (DNS rebinding) is refused
        status, _ = self.http("GET", "/api/board", headers={"Host": "evil.example:80"})
        self.assertEqual(status, 403)
        status, _ = self.http("POST", "/api/move", {"card_id": "K-1", "status": "done"}, {"X-Kanban": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(self.board()["cards"][0]["status"], "done")
        status, _ = self.http("POST", "/api/add", {"title": "From the board"}, {"X-Kanban": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(self.board()["cards"][1]["source"], "user")
        self.assertTrue((self.root / ".kanban" / "url").read_text().startswith("http://127.0.0.1:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
