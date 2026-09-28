#!/usr/bin/env python3
"""hooks/hooks.json + scripts/hook.py (PRD-03): PostToolUse heartbeat throttled to 1 per 10 s per session, 1 s
timeout, exit 0 always, immediate exit without daemon.json; Stop → waiting only for a non-terminal interactive run
(stdlib only; every home is a temp folder)."""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
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

HOOK = helpers.SCRIPTS / "hook.py"


def run_hook(home: Path, event: str, session: str = "sess-1", stdin: str | None = None, timeout: float = 10):
    payload = stdin if stdin is not None else json.dumps({"session_id": session, "hook_event_name": event,
                                                           "tool_name": "Bash", "cwd": str(home)})
    env = helpers.base_env(home)
    start = time.monotonic()
    out = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=env,
                         timeout=timeout)
    return out, time.monotonic() - start


class CountingServer:
    """A stand-in daemon that counts the hook's POSTs."""

    def __init__(self):
        self.posts = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.posts.append((self.path, self.headers.get("Authorization"), json.loads(body)))
                data = b'{"matched": 0}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class HookTests(unittest.TestCase):
    def setUp(self):
        self.home = helpers.temp_home()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def fake_daemon(self, port):
        (self.home / "daemon.json").write_text(json.dumps({"port": port, "pid": os.getpid(), "api": 1}))
        (self.home / "client.token").write_text("tok-123\n")

    def test_ancestors_walk_only_the_chain(self):
        """B5: at most 4 `ps -p <pid>` calls up the parent chain (no whole-process-table scan), same output."""
        sys.path.insert(0, str(helpers.SCRIPTS))
        import hook
        real, calls = subprocess.run, []

        def spy(argv, *a, **kw):
            calls.append(list(argv))
            return real(argv, *a, **kw)
        subprocess.run = spy  # hook imports subprocess lazily: patching the shared module object reaches it
        try:
            chain = hook.ancestors()
        finally:
            subprocess.run = real
        self.assertTrue(calls)
        self.assertLessEqual(len(calls), 4, calls)
        for argv in calls:
            self.assertNotIn("-A", argv)
            self.assertIn("-p", argv)
        self.assertEqual(chain[0], [os.getppid(), kd.process_start_time(os.getppid())])
        self.assertLessEqual(len(chain), 4)
        for pid, start in chain:
            self.assertIsInstance(pid, int)
            self.assertGreater(pid, 1)
            self.assertEqual(start, kd.process_start_time(pid))

    def test_hooks_json(self):
        spec = json.loads((helpers.PLUGIN / "hooks" / "hooks.json").read_text())
        for event in ("PostToolUse", "Stop"):
            entries = [h for group in spec["hooks"][event] for h in group["hooks"]]
            self.assertEqual([(h["type"], h["command"], h["timeout"]) for h in entries],
                             [("command", 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py"', 2)])

    def test_hook_throttle_timeout_fast_exit(self):
        # no daemon.json → exit 0 at once, nothing written into the home
        best = min(run_hook(self.home, "PostToolUse")[1] for _ in range(3))
        out, _ = run_hook(self.home, "PostToolUse")
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        self.assertLess(best, 0.2)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [])
        # daemon down: refused port, then a port that accepts but never answers (1 s timeout)
        self.fake_daemon(helpers.free_port())
        out, took = run_hook(self.home, "PostToolUse")
        self.assertEqual(out.returncode, 0)
        self.assertLess(took, 1.5)
        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen(8)
        try:
            self.fake_daemon(silent.getsockname()[1])
            out, took = run_hook(self.home, "Stop")
            self.assertEqual(out.returncode, 0)
            self.assertLess(took, 1.5)
        finally:
            silent.close()
        # garbage on stdin still exits 0
        self.assertEqual(run_hook(self.home, "PostToolUse", stdin="not json")[0].returncode, 0)
        # throttle: 10 calls within about a second → 1 POST; another session gets its own
        server = CountingServer()
        try:
            self.fake_daemon(server.port)
            start = time.monotonic()
            for _ in range(10):
                self.assertEqual(run_hook(self.home, "PostToolUse")[0].returncode, 0)
            self.assertLess(time.monotonic() - start, 10)
            self.assertEqual(len(server.posts), 1, server.posts)
            path, auth, body = server.posts[0]
            self.assertEqual((path, auth, body["event"]), ("/api/hooks/activity", "Bearer tok-123", "PostToolUse"))
            pids = [p for p, _ in body["pids"]]
            self.assertIn(os.getpid(), pids)  # the test process is an ancestor of the hook
            mine = dict((p, t) for p, t in body["pids"])[os.getpid()]
            self.assertEqual(mine, kd.process_start_time(os.getpid()))
            run_hook(self.home, "PostToolUse", session="sess-2")
            self.assertEqual(len(server.posts), 2)
            # Stop is never throttled and resets the session's throttle
            run_hook(self.home, "Stop")
            run_hook(self.home, "PostToolUse")
            self.assertEqual([p[2]["event"] for p in server.posts[2:]], ["Stop", "PostToolUse"])
        finally:
            server.close()


class StopHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kanban-hook-"))
        self.root = helpers.make_project(self.tmp)
        km.create_ticket(self.root, "Login")
        self.d = helpers.TestDaemon().start()
        self.store = kdb.Store(kdb.db_path(self.d.home))

    def tearDown(self):
        self.store.close()
        self.d.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stop_hook_sticky(self):
        status, body = self.d.call("POST", "/api/sessions", {
            "project_root": str(self.root), "claude_pid": os.getpid(),
            "claude_start_time": kd.process_start_time(os.getpid()), "kind": "interactive", "channel": True})
        sid, project = body["session_id"], body["project_id"]
        status, moved = self.d.call("POST", f"/api/projects/{project}/tickets/login/move", {"stage": "architect"},
                                    token="ui")
        status, claim = self.d.call("POST", f"/api/projects/{project}/tickets/login/claim",
                                    {"handoff_id": moved["handoff"]["id"]}, headers={"X-Kanban-Session": sid})
        run_id = claim["run_id"]
        state = lambda: kdb.get_run(self.store.conn(), run_id)["status"]  # noqa: E731
        self.assertEqual(state(), "running")
        self.assertEqual(run_hook(self.d.home, "Stop")[0].returncode, 0)
        self.assertEqual(state(), "waiting")
        run_hook(self.d.home, "PostToolUse")
        self.assertEqual(state(), "running")
        status, _ = self.d.call("POST", "/api/runs/finish", {"outcome": "done", "summary": "done"},
                                headers={"X-Kanban-Session": sid})
        self.assertEqual(state(), "succeeded")
        run_hook(self.d.home, "Stop")
        self.assertEqual(state(), "succeeded")  # terminal states are sticky
        events = [e["data"]["to"] for e in kdb.run_events(self.store.conn(), run_id) if e["type"] == "status"]
        self.assertEqual(events, ["running", "waiting", "running", "succeeded"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
