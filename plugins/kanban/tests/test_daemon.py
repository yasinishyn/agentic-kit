#!/usr/bin/env python3
"""Board daemon: single instance and version replace, events stream, live sessions, human moves and hand-offs,
approvals, plug-in contract, viewer and board render time (stdlib only). Every daemon runs in a temp KANBAN_HOME."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import helpers
import daemon as kd
import kanban_db as kdb
import kanban_md as km

PRD = "---\ntitle: PRD-{n:02d}\nstatus: todo\n---\n\n# PRD-{n:02d}\n\n- [x] one\n- [ ] two\n"


def ensure_cmd(home: Path, *args, timeout=30):
    return subprocess.run([sys.executable, str(helpers.DAEMON_PY), *args], capture_output=True, text=True,
                          timeout=timeout, env=helpers.base_env(home))


class Stream:
    """A reader of GET /api/events (newline-delimited JSON over a long-lived response)."""

    def __init__(self, port, token, project=None, session=None):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": f"127.0.0.1:{port}"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if session:
            headers["X-Kanban-Session"] = session
        self.conn.request("GET", "/api/events" + (f"?project={project}" if project else ""), headers=headers)
        self.sock = self.conn.sock  # http.client hands the socket to the response (no Content-Length)
        self.resp = self.conn.getresponse()
        self.status = self.resp.status

    def next(self, event=None, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            self.sock.settimeout(max(0.05, end - time.time()))
            try:
                line = self.resp.readline()
            except OSError:
                break
            if not line:
                break
            ev = json.loads(line)
            if event is None or ev["event"] == event:
                return ev
        return None

    def close(self):
        self.resp.close()
        self.conn.close()
        self.sock.close()


class DaemonCase(unittest.TestCase):
    routes_dirs = ""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kanban-daemon-"))
        self.root = helpers.make_project(self.tmp)
        km.create_ticket(self.root, "Login")
        extra = {"KANBAN_ROUTES_DIRS": self.routes_dirs} if self.routes_dirs else {}
        self.d = helpers.TestDaemon(**extra).start()
        self.session = self.d.register(self.root)
        self.pid = self.session["project_id"]

    def tearDown(self):
        self.d.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def move(self, stage, token="ui", ticket="login"):
        return self.d.call("POST", f"/api/projects/{self.pid}/tickets/{ticket}/move", {"stage": stage}, token=token)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.home = helpers.temp_home()

    def tearDown(self):
        helpers.kill_home_daemon(self.home)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_ensure_single_instance(self):
        procs = [subprocess.Popen([sys.executable, str(helpers.DAEMON_PY), "--ensure"], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=helpers.base_env(self.home))
                 for _ in range(2)]
        outs = [p.communicate(timeout=30) for p in procs]  # returns: the daemon holds none of our pipes
        self.assertEqual([p.returncode for p in procs], [0, 0], outs)
        info = helpers.read_json(self.home / "daemon.json")
        self.assertEqual(outs[0][0], f"http://127.0.0.1:{info['port']}/\n")
        self.assertEqual(outs[0][0], outs[1][0])
        self.assertEqual(os.getsid(info["pid"]), info["pid"])  # detached: its own session
        again = ensure_cmd(self.home, "--ensure")
        self.assertEqual(helpers.read_json(self.home / "daemon.json")["pid"], info["pid"])
        self.assertEqual(again.stdout, outs[0][0])
        second = ensure_cmd(self.home, "--foreground", "--port", "0", timeout=15)
        self.assertEqual(second.returncode, 3)  # flock: one daemon per KANBAN_HOME
        self.assertIn("another kanban daemon holds daemon.lock", second.stderr)
        # home and secrets
        self.assertEqual(stat.S_IMODE(os.stat(self.home).st_mode), 0o700)
        for name in ("client.token", "ui.token", "daemon.json", "kanban.db"):
            self.assertEqual(stat.S_IMODE(os.stat(self.home / name).st_mode), 0o600, name)
        text = (self.home / "daemon.json").read_text()
        self.assertEqual(sorted(json.loads(text)), ["api", "pid", "port", "started_at", "version"])
        for name in ("client.token", "ui.token"):
            self.assertNotIn((self.home / name).read_text().strip(), text)
        self.assertNotIn((self.home / "ui.token").read_text().strip(), (self.home / "daemon.log").read_text())

    def test_newer_version_replaces_only_an_idle_daemon(self):
        old = helpers.TestDaemon(home=self.home, KANBAN_DAEMON_VERSION="0.2.9").start()
        try:
            out = ensure_cmd(self.home, "--ensure")
            self.assertEqual(out.returncode, 0, out.stderr)
            old.proc.wait(timeout=10)  # the idle older daemon was replaced
            info = kd.probe(self.home)
            self.assertNotEqual(info["pid"], old.proc.pid)
            self.assertEqual(info["version"], kd.VERSION)
        finally:
            old.stop()
        helpers.kill_home_daemon(self.home)
        store = kdb.Store(kdb.db_path(self.home))
        root = helpers.make_project(self.home)
        pid = kdb.register_project(store.conn(), root)["id"]
        run = kdb.create_run(store.conn(), pid, "login", "developer", "interactive", status="running")
        busy = helpers.TestDaemon(home=self.home, KANBAN_DAEMON_VERSION="0.2.9").start()
        try:
            out = ensure_cmd(self.home, "--ensure")
            self.assertIn("kept daemon 0.2.9 while runs are live", out.stdout)
            self.assertEqual(kd.probe(self.home)["pid"], busy.proc.pid)
            refused = ensure_cmd(self.home, "--stop")
            self.assertEqual(refused.returncode, 1)
            self.assertIn("1 run(s) are live", refused.stdout)
            self.assertIsNone(busy.proc.poll())
            stopped = ensure_cmd(self.home, "--stop", "--cancel-runs")
            self.assertEqual((stopped.returncode, stopped.stdout), (0, "Stopped the board daemon.\n"))
            busy.proc.wait(timeout=10)
            self.assertEqual(kdb.get_run(store.conn(), run["id"])["status"], "cancelled")
            self.assertFalse((self.home / "daemon.json").exists())
        finally:
            busy.stop()
            store.close()

    def test_open_url_carries_the_ui_token_only_in_the_fragment(self):
        tokens = kd.ensure_tokens(kdb.prepare_home(self.home))
        info = {"port": 4321}
        self.assertEqual(kd.board_url(info), "http://127.0.0.1:4321/")
        self.assertEqual(kd.ui_url(info, self.home), "http://127.0.0.1:4321/#t=" + tokens["ui"])

    def test_concurrent_token_creation_never_yields_an_empty_token(self):
        code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import daemon, pathlib; "
                "print(json.dumps(daemon.ensure_tokens(pathlib.Path(sys.argv[2]))))")
        for _ in range(5):
            home = helpers.temp_home()
            try:
                procs = [subprocess.Popen([sys.executable, "-c", code, str(helpers.SCRIPTS), str(home)],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                          env=helpers.base_env(home)) for _ in range(8)]
                outs = [p.communicate(timeout=30) for p in procs]
                self.assertEqual([p.returncode for p in procs], [0] * 8, outs)
                seen = {o[0] for o in outs}
                self.assertEqual(len(seen), 1, "every caller reads the same published tokens")
                tokens = json.loads(seen.pop())
                self.assertTrue(tokens["client"] and tokens["ui"] and tokens["client"] != tokens["ui"])
                self.assertEqual(sorted(p.name for p in home.iterdir()), ["client.token", "ui.token"])
            finally:
                shutil.rmtree(home, ignore_errors=True)

    def test_stop_when_not_running(self):
        out = ensure_cmd(self.home, "--stop")
        self.assertEqual((out.returncode, out.stdout), (0, "The board daemon is not running.\n"))


class EventTests(DaemonCase):
    def test_events_stream_auth(self):
        status = helpers.request(self.d.port, "GET", "/api/events")[0]
        self.assertEqual(status, 401)
        stream = Stream(self.d.port, self.d.ui_token, self.pid)
        try:
            self.assertEqual(stream.status, 200)
            self.assertEqual(stream.resp.getheader("Content-Type"), "application/x-ndjson")
            self.assertEqual(stream.next()["event"], "hello")
            time.sleep(0.3)
            readme = self.root / ".SDD/specs/login/README.md"
            start = time.time()
            readme.write_text(readme.read_text() + "\nMore text.\n")
            ev = stream.next("board.changed", timeout=2.0)
            self.assertIsNotNone(ev, "board.changed within 2 s of a markdown change")
            self.assertLess(time.time() - start, 2.0)
            self.assertEqual(ev["project"], self.pid)
        finally:
            stream.close()

    def test_max_subscribers(self):
        streams = [Stream(self.d.port, self.d.client_token) for _ in range(kd.MAX_SUBSCRIBERS)]
        try:
            self.assertEqual({s.status for s in streams}, {200})
            extra = Stream(self.d.port, self.d.client_token)
            self.assertEqual(extra.status, 503)
            extra.close()
        finally:
            for s in streams:
                s.close()
        end = time.time() + 5
        while time.time() < end and self.d.get("/api/health")[1]["subscribers"]:
            time.sleep(0.1)
        self.assertEqual(self.d.get("/api/health")[1]["subscribers"], 0)

    def test_unknown_project_stream_404(self):
        self.assertEqual(Stream(self.d.port, self.d.ui_token, "nope").status, 404)


class PluginTests(DaemonCase):
    def setUp(self):
        self.broken = Path(tempfile.mkdtemp(prefix="kanban-routes-"))
        (self.broken / "routes_broken.py").write_text(
            "def register(ctx):\n"
            "    ctx.route('GET', '/api/broken', lambda req: {'bad': True}, 'read')\n"
            "    raise RuntimeError('boom')\n")
        (self.broken / "routes_zz_syntax.py").write_text("def register(ctx:\n")
        self.routes_dirs = os.pathsep.join([str(helpers.FIXTURES), str(self.broken)])
        super().setUp()

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.broken, ignore_errors=True)

    def test_plugin_contract(self):
        status, ping = self.d.get("/api/sample/ping")
        self.assertEqual((status, ping), (200, {"pong": True, "started": True, "guarded": True, "setting": "hello"}))
        plugins = self.d.get("/api/health")[1]["plugins"]
        self.assertEqual(plugins["failed"], ["routes_broken", "routes_zz_syntax"])
        # the shipped scripts/routes_*.py modules load too; anything else loaded must be the fixture
        shipped = sorted(p.stem for p in helpers.SCRIPTS.glob("routes_*.py"))
        self.assertIn("routes_sample", plugins["loaded"])
        self.assertEqual(sorted(n for n in plugins["loaded"] if n != "routes_sample"), shipped)
        self.assertEqual(self.d.get("/api/broken")[0], 404)  # routes of a failed module are discarded
        log = self.d.log_text()
        self.assertIn("plug-in routes_broken skipped: RuntimeError('boom')", log)
        self.assertIn("plug-in routes_zz_syntax skipped", log)
        # scopes apply to plug-in routes too
        self.assertEqual(self.d.call("POST", f"/api/projects/{self.pid}/sample/publish", {}, token="ui")[0], 403)
        self.assertEqual(self.d.get("/api/sample/ping", token=None)[0], 401)
        # publish reaches subscribers; on_human_move sees the move and its hand-off
        stream = Stream(self.d.port, self.d.ui_token, self.pid)
        try:
            self.assertEqual(self.d.call("POST", f"/api/projects/{self.pid}/sample/publish", {})[0], 200)
            self.assertEqual(stream.next("sample.event")["data"], {"folder": "proj"})
        finally:
            stream.close()
        self.assertEqual(self.move("architect")[0], 200)
        self.assertEqual(self.d.get(f"/api/projects/{self.pid}/sample/moves")[1],
                         {"moves": [[self.pid, "login", "discovery", "architect", "queued"]]})

    def test_live_sessions(self):
        channel = self.d.register(self.root, channel=True)["session_id"]
        live = f"/api/projects/{self.pid}/sample/live"
        self.assertEqual(self.d.get(live)[1], {"live": [], "channel": []})
        a = Stream(self.d.port, self.d.client_token, self.pid, session=self.session["session_id"])
        b = Stream(self.d.port, self.d.client_token, session=channel)
        a.next(), b.next()
        self.assertEqual(self.d.get(live)[1], {"live": sorted([self.session["session_id"], channel]),
                                               "channel": [channel]})
        a.close()
        end = time.time() + 3
        while time.time() < end and len(self.d.get(live)[1]["live"]) != 1:
            time.sleep(0.05)
        self.assertEqual(self.d.get(live)[1], {"live": [channel], "channel": [channel]})
        b.close()
        end = time.time() + 3
        while time.time() < end and self.d.get(live)[1]["live"]:
            time.sleep(0.05)
        self.assertEqual(self.d.get(live)[1], {"live": [], "channel": []})


class MoveAndApprovalTests(DaemonCase):
    def db(self):
        store = kdb.Store(kdb.db_path(self.d.home))
        self.addCleanup(store.close)
        return store.conn()

    def test_human_move_handoff_supersede_coalesce(self):
        stream = Stream(self.d.port, self.d.ui_token, self.pid)
        try:
            status, first = self.move("architect")
            self.assertEqual(status, 200, first)
            self.assertEqual((first["handoff"]["kind"], first["handoff"]["status"], first["handoff"]["actor"]),
                             ("start", "queued", "human (board)"))
            ev = stream.next("handoff.created")
            self.assertEqual((ev["data"]["id"], ev["data"]["stage"], ev["data"]["from_stage"]),
                             (first["handoff"]["id"], "architect", "discovery"))
        finally:
            stream.close()
        self.assertIn("status: architect", (self.root / ".SDD/specs/login/README.md").read_text())
        status, second = self.move("discovery")  # backward: rework, supersedes the unclaimed first one
        self.assertEqual((second["handoff"]["kind"], second["handoff"]["superseded"]),
                         ("rework", [first["handoff"]["id"]]))
        conn = self.db()
        self.assertEqual(kdb.get_handoff(conn, first["handoff"]["id"])["status"], "superseded")
        run = kdb.create_run(conn, self.pid, "login", "architect", "interactive", status="running")
        status, third = self.move("architect")  # the live run already works this stage
        self.assertEqual((third["handoff"]["status"], third["handoff"]["run_id"]), ("coalesced", run["id"]))
        status, fourth = self.move("approval")  # approval is not a working stage: no hand-off
        self.assertEqual((status, fourth["handoff"]), (200, None))
        status, refused = self.move("developer")  # needs an approval first; guarded server-side
        self.assertEqual((status, refused["reason"]), (409, "approval_required"))
        self.assertEqual(self.move("developer", token="client")[0], 403)
        self.assertEqual(self.move("bogus")[0], 400)
        self.assertEqual(self.move("qa", ticket="nope")[0], 404)
        self.assertEqual(self.move("qa", ticket="..")[0], 400)
        status, body = self.d.call("POST", f"/api/projects/{self.pid}/tickets/login/move",
                                   {"stage": "discovery", "actor": "claude"}, token="ui")
        self.assertEqual(body["handoff"]["actor"], "human (board)")  # actor never taken from the body

    def test_record_approval_both_actors(self):
        folder = self.root / ".SDD/specs/login"
        (folder / "03-architecture.md").write_text("# Architecture\n\n| a | b |\n|---|---|\n\nBody.\n")
        status, board = self.d.call("POST", f"/api/projects/{self.pid}/tickets/login/approve", {}, token="ui")
        self.assertEqual(status, 200, board)
        self.assertIn("approved_by: human (board)", (folder / "README.md").read_text())
        conn = self.db()
        row = kdb.latest_approval(conn, self.pid, "login")
        self.assertEqual((row["actor"], sorted(kdb.approval_files(conn, row["id"]))),
                         ("human (board)", ["03-architecture.md"]))
        ticket = self.d.get(f"/api/projects/{self.pid}/board")[1]["tickets"][0]
        self.assertEqual(ticket["approval"], {"state": "valid", "by": "human (board)", "at": km.today(),
                                              "recorded": True, "board_recorded": True, "legacy": False})
        sid = self.session["session_id"]
        chat = f"/api/projects/{self.pid}/tickets/login/approve-chat"
        self.assertEqual(self.d.call("POST", chat, {}, token="client")[0], 403)  # no session
        status, body = self.d.call("POST", chat, {}, token="client", headers={"X-Kanban-Session": sid})
        self.assertEqual(status, 200, body)
        self.assertIn("approved_by: human (chat)", (folder / "README.md").read_text())
        self.assertIn("Approved for execution by human (chat)", (folder / "03-architecture.md").read_text())
        row = kdb.latest_approval(conn, self.pid, "login")
        self.assertEqual((row["actor"], row["session_id"]), ("human (chat)", sid))
        self.assertEqual(sorted(kdb.approval_files(conn, row["id"])), ["03-architecture.md"])
        ticket = self.d.get(f"/api/projects/{self.pid}/board")[1]["tickets"][0]
        self.assertEqual((ticket["approval"]["recorded"], ticket["approval"]["board_recorded"]), (True, False))
        kdb.create_run(conn, self.pid, "login", "architect", "headless", claude_pid=os.getpid(),
                       claude_start_time=kd.process_start_time(os.getpid()))  # this process is a headless run's
        headless = self.d.register(self.root)["session_id"]
        status, body = self.d.call("POST", chat, {}, token="client", headers={"X-Kanban-Session": headless})
        self.assertEqual(status, 403)
        self.assertIn("headless", body["error"])
        other = helpers.make_project(self.tmp, "other")
        foreign = self.d.register(other)["session_id"]
        self.assertEqual(self.d.call("POST", chat, {}, token="client", headers={"X-Kanban-Session": foreign})[0], 403)
        self.assertEqual(kdb.latest_approval(conn, self.pid, "login")["actor"], "human (chat)")

    def test_approval_not_recorded_badge(self):
        km.approve(self.root, "login", "human (board)")  # README only, as after a deleted DB
        approval = self.d.get(f"/api/projects/{self.pid}/board")[1]["tickets"][0]["approval"]
        self.assertEqual((approval["state"], approval["recorded"]), ("valid", False))

    def test_pre_v03_ticket_in_execution(self):
        """Q14: a ticket already in EXECUTION with no approval record moves inside EXECUTION and shows a badge."""
        board = f"/api/projects/{self.pid}/board"
        self.assertFalse(self.d.get(board)[1]["tickets"][0]["approval"]["legacy"])  # discovery: no badge
        readme = self.root / ".SDD/specs/login/README.md"
        readme.write_text(km.set_fields(readme.read_text(), {"status": "developer"}))
        approval = self.d.get(board)[1]["tickets"][0]["approval"]
        self.assertEqual((approval["state"], approval["legacy"]), ("none", True))
        self.assertEqual(self.move("qa")[0], 200)
        self.assertEqual(self.move("done")[0], 200)
        self.assertEqual(self.move("architect")[0], 200)
        self.assertFalse(self.d.get(board)[1]["tickets"][0]["approval"]["legacy"])
        status, refused = self.move("developer")
        self.assertEqual((status, refused["reason"]), (409, "approval_required"))


class BoardTests(DaemonCase):
    def test_projects_board_and_viewer(self):
        status, projects = self.d.get("/api/projects", token="ui")
        self.assertEqual(projects["projects"], [{"id": self.pid, "name": "proj", "root": str(self.root)}])
        status, board = self.d.get(f"/api/projects/{self.pid}/board", token="ui")
        self.assertEqual([t["id"] for t in board["tickets"]], ["login"])
        self.assertEqual(board["stages"][0], ["discovery", "Discovery"])
        self.assertEqual(self.d.get("/api/projects/nope/board")[0], 404)
        prd = self.root / ".SDD/specs/login/prd"
        prd.mkdir()
        (prd / "PRD-01-x.md").write_text(
            "---\ntitle: <b>t</b>\n---\n\n# Hi <img src=x onerror=alert(1)>\n\n"
            "- [x] done `<script>`\n\n[bad](javascript:alert(1)) [spec](../README.md) [web](https://example.com)\n"
            "| <i>a</i> | b |\n|---|---|\n| c | d |\n")
        rel = ".SDD/specs/login/prd/PRD-01-x.md"
        status, view = self.d.get(f"/api/projects/{self.pid}/view?path={rel}", token="ui")
        self.assertEqual(status, 200, view)
        html = view["html"]
        self.assertNotIn("<img", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("<b>t</b>", html)
        self.assertNotIn("<i>a</i>", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertIn('<a href="#" data-md=".SDD/specs/login/README.md">spec</a>', html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn("<input type=checkbox disabled checked>", html)
        for bad in ("../../etc/passwd", ".SDD/specs/login", "/etc/hosts"):
            self.assertEqual(self.d.get(f"/api/projects/{self.pid}/view?path={bad}")[0], 404, bad)

    def test_new_project_is_announced(self):
        """B12: registering a new project publishes project.registered to every board, whatever project it watches."""
        stream = Stream(self.d.port, self.d.ui_token, self.pid)
        try:
            stream.next("hello")
            other = helpers.make_project(self.tmp, "other")
            status, body = self.d.call("POST", "/api/projects", {"project_root": str(other)})
            self.assertEqual(status, 200, body)
            ev = stream.next("project.registered")
            self.assertIsNotNone(ev)
            self.assertEqual(ev["data"], {"id": body["project"]["id"], "name": "other"})
            self.d.call("POST", "/api/projects", {"project_root": str(other)})  # known: no second event
            third = helpers.make_project(self.tmp, "third")
            self.d.register(third)  # a session in a new project announces it too
            ev = stream.next("project.registered")
            self.assertEqual(ev["data"]["name"], "third")
        finally:
            stream.close()
        app = helpers.request(self.d.port, "GET", "/ui/app.js")[2].decode()
        self.assertIn('ev.event === "project.registered"', app)  # the board refreshes its project list
        self.assertIn("refreshProjects()", app)

    def test_new_ticket_from_the_board(self):
        """B14: the v0.2 "New ticket" form is back — a UI-token route creates the spec folder in Discovery."""
        path = f"/api/projects/{self.pid}/tickets"
        status, body = self.d.call("POST", path, {"title": "Weekly export"}, token="ui")
        self.assertEqual(status, 200, body)
        self.assertEqual((body["ticket"]["id"], body["ticket"]["status"]), ("weekly-export", "discovery"))
        self.assertIn("status: discovery", (self.root / ".SDD/specs/weekly-export/README.md").read_text())
        self.assertEqual(self.d.call("POST", path, {"title": "Weekly export"}, token="ui")[0], 409)  # never overwrites
        self.assertEqual(self.d.call("POST", path, {"title": "   "}, token="ui")[0], 400)
        self.assertEqual(self.d.call("POST", path, {"title": "x" * 121}, token="ui")[0], 400)
        self.assertEqual(self.d.call("POST", path, {"title": "From Claude"})[0], 403)  # client token: human-only route
        self.assertFalse((self.root / ".SDD/specs/from-claude").exists())
        html = helpers.request(self.d.port, "GET", "/")[2].decode()
        app = helpers.request(self.d.port, "GET", "/ui/app.js")[2].decode()
        self.assertIn('id="new-ticket"', html)  # the header form
        self.assertIn("/tickets`", app)

    def test_project_root_must_be_a_project(self):
        """B9: a project root is an existing directory holding .SDD, .git or .claude; anything else is 400."""
        bare = self.tmp / "bare"
        bare.mkdir()
        (self.tmp / "file.txt").write_text("x")
        before = self.d.get("/api/projects")[1]["projects"]
        for root in (bare, self.tmp / "missing", self.tmp / "file.txt", "relative/path", ""):
            for path, extra in (("/api/projects", {}), ("/api/sessions", {"claude_pid": os.getpid()})):
                status, body = self.d.call("POST", path, {"project_root": str(root), **extra})
                self.assertEqual(status, 400, (path, root, body))
        self.assertEqual(self.d.get("/api/projects")[1]["projects"], before)
        for marker in (".SDD", ".claude"):
            ok = self.tmp / f"ok{marker}"
            (ok / marker).mkdir(parents=True)
            self.assertEqual(self.d.call("POST", "/api/projects", {"project_root": str(ok)})[0], 200)

    def test_board_cache_follows_markdown_edits(self):
        folder = self.root / ".SDD/specs/login"
        (folder / "prd").mkdir()
        prd = folder / "prd/PRD-01-x.md"
        prd.write_text(PRD.format(n=1))
        km.create_ticket(self.root, "Signup")
        store = kdb.Store(kdb.db_path(self.d.home))
        try:
            conn, project = store.conn(), kdb.get_project(store.conn(), self.pid)

            def board():
                return kd.build_board(conn, project)["tickets"]
            first = board()
            self.assertEqual([{k: v for k, v in t.items() if k != "approval"} for t in first],
                             km.list_tickets(self.root))
            self.assertEqual(first[0]["subtasks"][0]["done"], 1)
            km.check(self.root, ".SDD/specs/login/prd/PRD-01-x.md", 2)  # same size, new content (atomic replace)
            self.assertEqual(board()[0]["subtasks"][0]["done"], 2)
            km.set_status(self.root, ".SDD/specs/login/prd/PRD-01-x.md", "doing")
            self.assertEqual(board()[0]["subtasks"][0]["status"], "doing")
            (folder / "tasks").mkdir()
            (folder / "tasks/01-new.md").write_text("# New task\n")
            self.assertEqual([s["title"] for s in board()[0]["subtasks"]], ["PRD-01", "New task"])
            km.approve(self.root, "signup", "human (board)")
            self.assertEqual(board()[1]["approval"]["state"], "valid")
            (self.root / ".SDD/specs/signup/01-discovery.md").write_text("# Changed spec\n")
            self.assertEqual(board()[1]["approval"]["state"], "changed")
            shutil.rmtree(folder)
            self.assertEqual([t["id"] for t in board()], ["signup"])
        finally:
            store.close()

    def test_board_json_render_time(self):
        root = helpers.make_project(self.tmp, "big")
        for i in range(10):
            folder = km.specs_dir(root) / f"ticket-{i:02d}"
            (folder / "prd").mkdir(parents=True)
            (folder / "README.md").write_text(km.ticket_template(f"Ticket {i}", "Goal."))
            (folder / "03-architecture.md").write_text("# Arch\n\nText.\n")
            for n in range(50):
                (folder / "prd" / f"PRD-{n:02d}-x.md").write_text(PRD.format(n=n))
        km.approve(root, "ticket-00", "human (board)")
        store = kdb.Store(kdb.db_path(self.d.home))
        try:
            project = kdb.register_project(store.conn(), root)
            kd.build_board(store.conn(), project)  # warm the file cache like a running daemon
            start = time.perf_counter()
            board = kd.build_board(store.conn(), project)
            elapsed = time.perf_counter() - start
        finally:
            store.close()
        self.assertEqual(sum(len(t["subtasks"]) for t in board["tickets"]), 500)
        self.assertLess(elapsed, 0.2, f"board JSON took {elapsed * 1000:.0f} ms")


if __name__ == "__main__":
    unittest.main(verbosity=2)
