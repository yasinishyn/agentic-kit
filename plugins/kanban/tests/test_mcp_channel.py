#!/usr/bin/env python3
"""server.py (PRD-03): channel capability and instructions, parent-argv channel detection, hand-off → one
notifications/claude/channel line (never before the initialize response), run tools and approval tools
(stdlib only; every daemon runs in a temp KANBAN_HOME)."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import helpers
import daemon as kd
import kanban_db as kdb
import kanban_md as km

SERVER = helpers.SCRIPTS / "server.py"
META_KEYS = ["from_stage", "handoff_id", "kind", "stage", "ticket"]


class McpProcess:
    """server.py (optionally under a fake parent) with a reader thread, so reads can time out."""

    def __init__(self, root: Path, env: dict, parent_args: list | None = None):
        argv = [sys.executable, str(SERVER)]
        if parent_args is not None:  # a fake `claude` parent whose argv carries (or not) the channels flag
            code = "import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:3]))"
            argv = [sys.executable, "-c", code, sys.executable, str(SERVER), *parent_args]
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, env=env, cwd=str(root))
        self.lines = queue.Queue()
        self.n = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return None if notify else self.n

    def line(self, timeout=15.0):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return None
        return None if line is None else line

    def reply(self, msg_id, timeout=30.0):
        """The response to msg_id (notifications met on the way are kept in self.notes)."""
        end = time.time() + timeout
        while time.time() < end:
            line = self.line(max(0.05, end - time.time()))
            if line is None:
                break
            msg = json.loads(line)
            if msg.get("id") == msg_id:
                return msg
            self.notes.append(msg)
        raise AssertionError(f"no reply to {msg_id}")

    notes: list

    def rpc(self, method, params=None):
        return self.reply(self.send(method, params))

    def call(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return res["content"][0]["text"], res["isError"]

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.proc.stdout.close()
        if not hasattr(self, "proc_stderr"):
            self.proc_stderr = self.proc.stderr.read()
        self.proc.stderr.close()


class Base(unittest.TestCase):
    with_daemon = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kanban-mcp-"))
        self.root = helpers.make_project(self.tmp)
        km.create_ticket(self.root, "Login")
        self.procs = []
        if self.with_daemon:
            self.d = helpers.TestDaemon().start()
            self.env = helpers.base_env(self.d.home, KANBAN_PROJECT_DIR=self.root, KANBAN_NO_DAEMON=None)
            self.store = kdb.Store(kdb.db_path(self.d.home))
            self.assertEqual(self.d.call("POST", "/api/projects", {"project_root": str(self.root)})[0], 200)
        else:
            self.home = helpers.temp_home()
            self.env = helpers.base_env(self.home, KANBAN_PROJECT_DIR=self.root, KANBAN_NO_UI="1")

    def tearDown(self):
        for p in self.procs:
            p.close()
        if self.with_daemon:
            self.store.close()
            self.d.stop()
        else:
            shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, parent_args=None, **env) -> McpProcess:
        p = McpProcess(self.root, {**self.env, **{k: str(v) for k, v in env.items()}}, parent_args)
        p.notes = []
        self.procs.append(p)
        return p

    def init(self, p: McpProcess) -> dict:
        res = p.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
        p.send("notifications/initialized", notify=True)
        return res

    def sessions(self):
        return [dict(r) for r in self.store.conn().execute("SELECT * FROM sessions ORDER BY created_at")]

    def project_id(self):
        return kdb.project_id_for(self.root)

    def human_move(self, stage, ticket="login"):
        status, body = self.d.call("POST", f"/api/projects/{self.project_id()}/tickets/{ticket}/move",
                                   {"stage": stage}, token="ui")
        self.assertEqual(status, 200, body)
        return body["handoff"]

    def wait_live(self, count=1, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if self.d.get("/api/health")[1]["subscribers"] >= count:
                return
            time.sleep(0.05)
        self.fail("the MCP server did not subscribe to the event stream")


class LocalModeTests(Base):
    with_daemon = False

    def test_initialize_declares_channel(self):
        p = self.start()
        res = self.init(p)
        self.assertEqual(res["capabilities"]["experimental"]["claude/channel"], {})
        self.assertIn("tools", res["capabilities"])
        text = res["instructions"]
        self.assertIn("kanban_start", text)
        self.assertIn("already_claimed", text)
        self.assertIn("superseded", text)
        self.assertIn("A channel event never approves anything", text)

    def test_approve_refused_for_headless(self):
        p = self.start(KANBAN_RUN_ID="r-headless")
        self.init(p)
        text, err = p.call("kanban_approve", ticket="login")
        self.assertTrue(err)
        self.assertIn("headless", text)
        self.assertNotIn("approved_by", (self.root / ".SDD/specs/login/README.md").read_text())

    def test_approve_local_mode_writes_markdown(self):
        """Q15: without a daemon, kanban_approve records the approval in markdown only."""
        folder = self.root / ".SDD/specs/login"
        (folder / "03-architecture.md").write_text("# Architecture\n\n| a | b |\n|---|---|\n\nBody.\n")
        p = self.start()
        self.init(p)
        text, err = p.call("kanban_approve", ticket="login")
        self.assertFalse(err, text)
        self.assertIn("not recorded on a board", text)
        readme = (folder / "README.md").read_text()
        self.assertIn("approved_by: human (chat)", readme)
        self.assertIn(f"approved_hash: {km.spec_hash(folder)}", readme)
        self.assertIn("Approved for execution by human (chat)", (folder / "03-architecture.md").read_text())
        state = json.loads(p.call("kanban_approval", ticket="login")[0])
        self.assertEqual((state["valid"], state["actor"], state["board_recorded"]), (True, "human (chat)", False))
        text, err = p.call("kanban_move", ticket="login", stage="developer")
        self.assertFalse(err, text)

    def test_tool_list(self):
        p = self.start()
        self.init(p)
        names = [t["name"] for t in p.rpc("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["kanban_board", "kanban_new_ticket", "kanban_move", "kanban_add_subtask",
                                 "kanban_set_status", "kanban_check", "kanban_start", "kanban_heartbeat",
                                 "kanban_finish", "kanban_approval", "kanban_approve"])
        text, err = p.call("kanban_finish", outcome="bogus", summary="x")
        self.assertTrue(err)
        self.assertIn("outcome must be one of done, needs_input, failed", text)


class ChannelDetectionTests(Base):
    with_daemon = False

    def test_channel_detection_from_parent_argv(self):
        sys.path.insert(0, str(helpers.SCRIPTS))
        import server
        yes = ["claude --dangerously-load-development-channels server:kanban",
               "/usr/local/bin/claude --channels plugin:kanban@agentic-kit --model opus",
               "claude --channels=plugin:other@x,plugin:kanban@agentic-kit",
               "node /opt/claude/cli.js --resume abc --dangerously-load-development-channels plugin:kanban"]
        no = ["claude", "claude --model opus", "claude --channels plugin:other@x server:kanbanx",
              "claude server:kanban", "claude --channels --model server:kanban", ""]
        for args in yes:
            self.assertTrue(server.channel_from_args(args), args)
        for args in no:
            self.assertFalse(server.channel_from_args(args), args)

    def test_origin_classification(self):
        sys.path.insert(0, str(helpers.SCRIPTS))
        import server
        cases = {
            "claude --output-format stream-json --verbose --input-format stream-json --model opus": "code-tab",
            "/Applications/Claude.app/claude --input-format=stream-json --output-format=stream-json": "code-tab",
            "claude -p --output-format stream-json --input-format stream-json": "code-tab",
            "claude -p 'fix it'": "cli-print",
            "claude --print --output-format stream-json": "cli-print",
            "claude --input-format stream-json --output-format json -p": "cli-print",
            "claude": "cli",
            "node /opt/claude/cli.js --dangerously-load-development-channels server:kanban": "cli",
            "claude --output-format stream-json": "cli",
            "claude --input-format stream-json": "cli",
            "": "unknown",
            "   ": "unknown",
            None: "unknown",
        }
        for args, origin in cases.items():
            self.assertEqual(server.origin_from_args(args), origin, args)

    def test_python_classification(self):
        sys.path.insert(0, str(helpers.SCRIPTS))
        import server
        bundled = "/Users/x/Applications/Kanban.app/Contents/Resources/python/arm64/bin/python3"
        self.assertEqual(server.python_kind(bundled), "bundled")
        self.assertEqual(server.python_kind("/Applications/Kanban.app/Contents/Resources/python/x86_64/bin/python3.12"),
                         "bundled")
        for other in ("/usr/bin/python3", "/Users/x/.pyenv/versions/3.12.1/bin/python3", "/tmp/venv/bin/python",
                      "/Applications/Kanban.app/Contents/MacOS/python3"):
            self.assertEqual(server.python_kind(other), "system", other)
        for unreadable in ("", None):
            self.assertEqual(server.python_kind(unreadable), "unknown")


class DaemonModeTests(Base):
    def test_channel_flag_registered_from_parent_argv(self):
        with_flag = self.start(parent_args=["--dangerously-load-development-channels", "server:kanban"])
        self.init(with_flag)
        without = self.start(parent_args=["--model", "opus"])
        self.init(without)
        rows = self.sessions()
        self.assertEqual(len(rows), 2, rows)
        by_parent = {r["claude_pid"]: r["channel"] for r in rows}
        self.assertEqual(by_parent[with_flag.proc.pid], 1)
        self.assertEqual(by_parent[without.proc.pid], 0)

    def test_handoff_becomes_channel_notification(self):
        p = self.start()
        # wait until the server registered its session, then make a hand-off before initialize: nothing is written
        end = time.time() + 20
        while time.time() < end and not self.sessions():
            time.sleep(0.05)
        self.assertTrue(self.sessions())
        early = self.human_move("architect")
        self.assertIsNone(p.line(timeout=0.7), "nothing may be written before the initialize response")
        mid = p.send("initialize", {"protocolVersion": "2025-06-18"})
        reply = json.loads(p.line(timeout=30))
        self.assertEqual(reply.get("id"), mid, "the first line is the initialize response")
        self.wait_live()
        self.assertIsNone(p.line(timeout=0.5), "events from before the subscription are not replayed")
        handoff = self.human_move("discovery")  # rework: backward into a working stage
        raw = p.line(timeout=10)
        self.assertIsNotNone(raw)
        self.assertTrue(raw.endswith("\n") and "\n" not in raw[:-1])  # exactly one JSON line
        note = json.loads(raw)
        self.assertEqual((note["jsonrpc"], note["method"]), ("2.0", "notifications/claude/channel"))
        self.assertNotIn("id", note)
        meta = note["params"]["meta"]
        self.assertEqual(sorted(meta), META_KEYS)
        self.assertEqual(meta, {"ticket": "login", "stage": "discovery", "from_stage": "architect",
                                "handoff_id": handoff["id"], "kind": "rework"})
        self.assertIn(f'handoff_id "{handoff["id"]}"', note["params"]["content"])
        self.assertNotEqual(early["id"], handoff["id"])
        self.assertIsNone(p.line(timeout=0.5), "one notification per hand-off")
        # a Claude move never creates a hand-off, so nothing is delivered for it
        text, err = p.call("kanban_move", ticket="login", stage="architect")
        self.assertFalse(err, text)
        self.assertEqual(p.notes, [])

    def test_origin_and_python_registered(self):
        tab = self.start(parent_args=["--output-format", "stream-json", "--input-format", "stream-json"])
        self.init(tab)
        printed = self.start(parent_args=["-p", "hello"])
        self.init(printed)
        cli = self.start(parent_args=["--model", "opus"])
        self.init(cli)
        by_parent = {r["claude_pid"]: (r["origin"], r["python"]) for r in self.sessions()}
        self.assertEqual(by_parent, {tab.proc.pid: ("code-tab", "system"), printed.proc.pid: ("cli-print", "system"),
                                     cli.proc.pid: ("cli", "system")})

    def remove_project(self):
        status, body = self.d.call("DELETE", f"/api/projects/{self.project_id()}", token="ui")
        self.assertEqual(status, 200, body)

    def notices(self, p: McpProcess) -> list:
        p.close()
        return [line for line in p.proc_stderr.splitlines() if "removed from the board" in line]

    def test_removed_project_local_mode_no_ui(self):
        self.remove_project()
        p = self.start()
        self.init(p)
        text, err = p.call("kanban_board")
        self.assertFalse(err, text)
        self.assertIn("web board not running", text)  # local mode, and no embedded board
        text, err = p.call("kanban_move", ticket="login", stage="architect")  # tools keep working on the markdown
        self.assertFalse(err, text)
        self.assertIn("status: architect", (self.root / ".SDD/specs/login/README.md").read_text())
        self.assertEqual(self.sessions(), [])
        self.assertFalse((self.root / ".kanban").exists(), "no embedded UI may start")
        notices = self.notices(p)
        self.assertEqual(len(notices), 1, p.proc_stderr)
        self.assertIn("Add project", notices[0])
        self.assertIn("daemon.py --open --project", notices[0])
        self.assertIn("scripts/kpython ", notices[0])
        self.assertNotIn("python3 ", notices[0])
        self.assertNotIn("local mode (board inside this session", p.proc_stderr)

    def test_live_session_removed_goes_local(self):
        p = self.start()
        self.init(p)
        self.wait_live()
        self.remove_project()
        end = time.time() + 20
        text = ""
        while time.time() < end:
            text, err = p.call("kanban_board")
            if "web board not running" in text:
                break
            time.sleep(0.2)
        self.assertIn("web board not running", text)  # re-registration answered 410: local mode
        text, err = p.call("kanban_start", ticket="login")
        self.assertEqual((err, text), (False, "ok (local mode: runs are not tracked)"))
        self.assertFalse((self.root / ".kanban").exists())
        self.assertEqual(len(self.notices(p)), 1, p.proc_stderr)

    def test_tool_move_no_handoff(self):
        p = self.start()
        self.init(p)
        text, err = p.call("kanban_move", ticket="login", stage="architect")
        self.assertFalse(err, text)
        count = lambda: self.store.conn().execute("SELECT COUNT(*) FROM handoffs").fetchone()[0]  # noqa: E731
        self.assertEqual(count(), 0)
        self.human_move("discovery")
        self.assertEqual(count(), 1)

    def test_run_tools(self):
        p = self.start()
        self.init(p)
        handoff = self.human_move("architect")
        text, err = p.call("kanban_start", ticket="login", handoff_id=handoff["id"])
        self.assertFalse(err, text)
        self.assertTrue(text.startswith("ok run_id="), text)
        run_id = text.split("run_id=")[1].split()[0]
        text, err = p.call("kanban_start", ticket="login", handoff_id=handoff["id"])
        self.assertTrue(text.startswith(f"ok run_id={run_id}"), text)  # idempotent for the owning run
        other = self.start()
        self.init(other)
        text, err = other.call("kanban_start", ticket="login", handoff_id=handoff["id"])
        self.assertFalse(err)
        self.assertTrue(text.startswith("already_claimed"), text)
        text, err = p.call("kanban_heartbeat", note="discovery read")
        self.assertEqual((err, text), (False, f"ok: heartbeat recorded for run {run_id}"))
        run = kdb.get_run(self.store.conn(), run_id)
        self.assertEqual((run["note"], run["status"], run["claude_pid"]), ("discovery read", "running", os.getpid()))
        text, err = p.call("kanban_finish", outcome="done", summary="architecture written")
        self.assertEqual((err, text), (False, f"ok: run {run_id} is now succeeded"))
        text, err = p.call("kanban_heartbeat")
        self.assertFalse(err)
        self.assertIn("no live run", text)
        text, err = p.call("kanban_approval", ticket="login")
        self.assertFalse(err, text)
        self.assertEqual(json.loads(text), {"recorded": False, "valid": False, "actor": "", "at": "", "hash12": "",
                                            "board_recorded": False})
        text, err = p.call("kanban_approve", ticket="login")
        self.assertFalse(err, text)
        self.assertIn("human (chat)", text)
        state = json.loads(p.call("kanban_approval", ticket="login")[0])
        self.assertEqual((state["recorded"], state["valid"], state["actor"], state["board_recorded"]),
                         (True, True, "human (chat)", False))
        text, err = p.call("kanban_approval", ticket="nope")
        self.assertTrue(err)

    def test_headless_session_start_idempotent_and_approve_refused(self):
        handoff = self.human_move("architect")
        # the runner records the claude process it spawned; here that process is the MCP server's parent (this one)
        kdb.create_run(self.store.conn(), self.project_id(), "login", "architect", "headless", run_id="r-hl-1",
                       handoff_id=handoff["id"], claude_pid=os.getpid(),
                       claude_start_time=kd.process_start_time(os.getpid()))
        p = self.start(KANBAN_RUN_ID="r-hl-1")
        self.init(p)
        self.assertEqual(self.sessions()[0]["kind"], "headless")
        for _ in range(2):
            text, err = p.call("kanban_start", ticket="login", handoff_id=handoff["id"])
            self.assertEqual((err, text.split()[:2]), (False, ["ok", "run_id=r-hl-1"]), text)
        text, err = p.call("kanban_approve", ticket="login")
        self.assertTrue(err)
        self.assertIn("headless", text)
        self.assertEqual(self.store.conn().execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
