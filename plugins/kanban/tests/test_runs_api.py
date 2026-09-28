#!/usr/bin/env python3
"""routes_runs.py (PRD-03): claim, heartbeat, finish, hook activity, approval read, run lists and the pickup timer,
against a throwaway daemon in a temp KANBAN_HOME (stdlib only)."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import helpers
import daemon as kd
import kanban_db as kdb
import kanban_md as km


class Stream:
    """A reader of GET /api/events (newline-delimited JSON over a long-lived response)."""

    def __init__(self, port, token, project=None, session=None):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": f"127.0.0.1:{port}", "Authorization": f"Bearer {token}"}
        if session:
            headers["X-Kanban-Session"] = session
        self.conn.request("GET", "/api/events" + (f"?project={project}" if project else ""), headers=headers)
        self.sock = self.conn.sock
        self.resp = self.conn.getresponse()
        self.status = self.resp.status
        self.seen = []

    def next(self, event=None, timeout=5.0, where=None):
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
            self.seen.append(ev)
            if (event is None or ev["event"] == event) and (where is None or where(ev)):
                return ev
        return None

    def close(self):
        self.resp.close()
        self.conn.close()
        self.sock.close()


def wait_for(predicate, timeout=5.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


class RunsCase(unittest.TestCase):
    daemon_env: dict = {}  # extra daemon env per test class

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kanban-runs-"))
        self.root = helpers.make_project(self.tmp)
        km.create_ticket(self.root, "Login")
        km.create_ticket(self.root, "Signup")
        self.d = helpers.TestDaemon(KANBAN_SWEEP_SECONDS="0.5", **self.daemon_env).start()
        self.session = self.d.register(self.root)
        self.pid = self.session["project_id"]
        self.store = kdb.Store(kdb.db_path(self.d.home))

    def tearDown(self):
        self.store.close()
        self.d.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def db(self):
        return self.store.conn()

    def move(self, stage, ticket="login", token="ui"):
        status, body = self.d.call("POST", f"/api/projects/{self.pid}/tickets/{ticket}/move", {"stage": stage},
                                   token=token)
        self.assertEqual(status, 200, body)
        return body["handoff"]

    def session_for(self, kind="interactive", channel=False, run_id=None, pid=None, start=""):
        status, body = self.d.call("POST", "/api/sessions", {
            "project_root": str(self.root), "claude_pid": pid or os.getpid(), "claude_start_time": start, "kind": kind,
            "channel": channel, "run_id": run_id})
        self.assertEqual(status, 200, body)
        return body["session_id"]

    def claim(self, session, ticket="login", handoff_id=None, subtask=None):
        body = {k: v for k, v in (("handoff_id", handoff_id), ("subtask", subtask)) if v is not None}
        return self.d.call("POST", f"/api/projects/{self.pid}/tickets/{ticket}/claim", body,
                           headers={"X-Kanban-Session": session})

    def post(self, path, body, session):
        return self.d.call("POST", path, body, headers={"X-Kanban-Session": session})


class ClaimTests(RunsCase):
    def test_claim_atomic_and_idempotent(self):
        a, b = self.session_for(pid=os.getpid()), self.session_for(pid=os.getpid())
        handoff = self.move("architect")
        results = {}

        def worker(name, sid):
            results[name] = self.claim(sid, handoff_id=handoff["id"])
        threads = [threading.Thread(target=worker, args=(n, s)) for n, s in (("a", a), ("b", b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        answers = sorted(r[1]["result"] for r in results.values())
        self.assertEqual(answers, ["already_claimed", "ok"], results)
        self.assertEqual({r[0] for r in results.values()}, {200})
        runs = self.db().execute("SELECT * FROM runs WHERE ticket='login'").fetchall()
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]["status"], runs[0]["stage"], runs[0]["kind"]), ("running", "architect",
                                                                                 "interactive"))
        winner = a if results["a"][1]["result"] == "ok" else b
        loser = b if winner == a else a
        again = self.claim(winner, handoff_id=handoff["id"])[1]
        self.assertEqual((again["result"], again["run_id"]), ("ok", runs[0]["id"]))  # the owning run re-claims
        self.assertEqual(self.claim(loser, handoff_id=handoff["id"])[1]["result"], "already_claimed")
        self.assertEqual(kdb.get_handoff(self.db(), handoff["id"])["status"], "claimed")
        # a newer human move supersedes an unclaimed hand-off
        first = self.move("architect", ticket="signup")
        self.move("discovery", ticket="signup")
        self.assertEqual(self.claim(a, ticket="signup", handoff_id=first["id"])[1]["result"], "superseded")
        self.assertEqual(self.claim(a, ticket="signup", handoff_id="h-nope")[1]["result"], "not_found")
        self.assertEqual(self.claim(a, ticket="login", handoff_id=first["id"])[0], 400)  # other ticket's hand-off

    def headless_run(self, run_id, ticket="login", stage="architect", handoff_id=None, pid=None, start=None):
        """A live headless run whose claude process is `pid` (default: this test process), as runner.py records it."""
        pid = pid or os.getpid()
        return kdb.create_run(self.db(), self.pid, ticket, stage, "headless", handoff_id=handoff_id, run_id=run_id,
                              claude_pid=pid, claude_start_time=kd.process_start_time(pid) if start is None else start)

    def session_row(self, sid):
        return kdb.get_session(self.db(), sid)

    def test_headless_claim_idempotent_via_run_id(self):
        handoff = self.move("architect")
        self.headless_run("r-headless1", handoff_id=handoff["id"])
        sid = self.session_for(kind="headless", run_id="r-headless1")
        for _ in range(2):
            status, body = self.claim(sid, handoff_id=handoff["id"])
            self.assertEqual((status, body["result"], body["run_id"]), (200, "ok", "r-headless1"))
        self.assertEqual(kdb.get_run(self.db(), "r-headless1")["kind"], "headless")

    def test_session_kind_derived_server_side(self):
        """B4: kind/run_id come from a live headless run's process (pid + start time), never from the body."""
        sid = self.session_for(kind="headless", run_id="r-ghost")  # no headless run owns this process
        row = self.session_row(sid)
        self.assertEqual((row["kind"], row["run_id"]), ("interactive", None))
        other = self.headless_run("r-other", ticket="signup", pid=1, start="Thu Jan  1 00:00:00 1970")
        sid = self.session_for(kind="headless", run_id=other["id"])  # another process's run: ignored
        self.assertEqual((self.session_row(sid)["kind"], self.session_row(sid)["run_id"]), ("interactive", None))
        self.headless_run("r-reused", start="Thu Jan  1 00:00:00 1970")  # same pid, other start time: pid reuse
        sid = self.session_for(kind="headless", run_id="r-reused")
        self.assertEqual((self.session_row(sid)["kind"], self.session_row(sid)["run_id"]), ("interactive", None))
        kdb.transition_run(self.db(), "r-reused", "cancelled")
        mine = self.headless_run("r-mine")
        sid = self.session_for(kind="interactive", run_id=other["id"])  # the body's kind and run_id are ignored
        self.assertEqual((self.session_row(sid)["kind"], self.session_row(sid)["run_id"]), ("headless", mine["id"]))
        status, body = self.post(f"/api/projects/{self.pid}/tickets/login/approve-chat", {}, sid)
        self.assertEqual(status, 403, body)
        self.assertIn("headless", body["error"])
        self.assertIsNone(kdb.latest_approval(self.db(), self.pid, "login"))

    def test_claim_without_handoff_and_scopes(self):
        sid = self.session_for()
        status, body = self.claim(sid, subtask=".SDD/specs/login/prd/PRD-01-a.md")
        self.assertEqual((status, body["result"]), (200, "ok"), body)
        run = kdb.get_run(self.db(), body["run_id"])
        self.assertEqual((run["stage"], run["status"], run["handoff_id"]), ("discovery", "running", None))
        self.assertEqual(self.claim(sid)[1]["run_id"], run["id"])  # same session: same run
        self.assertEqual(self.claim(self.session_for())[1]["result"], "already_claimed")
        # no session header → refused; the UI token cannot claim; unknown ticket → 404
        self.assertEqual(self.d.call("POST", f"/api/projects/{self.pid}/tickets/login/claim", {})[0], 403)
        self.assertEqual(self.d.call("POST", f"/api/projects/{self.pid}/tickets/login/claim", {}, token="ui",
                                     headers={"X-Kanban-Session": sid})[0], 403)
        self.assertEqual(self.claim(sid, ticket="nope")[0], 404)


class LifecycleTests(RunsCase):
    def test_heartbeat_finish_and_requeue(self):
        sid = self.session_for()
        stream = Stream(self.d.port, self.d.ui_token, self.pid)
        try:
            stream.next("hello")
            handoff = self.move("architect")
            run_id = self.claim(sid, handoff_id=handoff["id"])[1]["run_id"]
            self.assertEqual(stream.next("run.changed")["data"]["run"]["status"], "running")
            status, body = self.post("/api/runs/heartbeat", {"note": "ADR-001 written"}, sid)
            self.assertEqual((status, body["ok"], body["run_id"]), (200, True, run_id))
            self.assertEqual(kdb.get_run(self.db(), run_id)["note"], "ADR-001 written")
            back = self.move("discovery")  # moved back while the run works architect: queued, not coalesced
            self.assertEqual(back["status"], "queued")
            status, body = self.post("/api/runs/finish", {"outcome": "needs_input", "summary": "Q3 open"}, sid)
            self.assertEqual((status, body["status"]), (200, "waiting"), body)
            status, body = self.post("/api/runs/heartbeat", {}, sid)
            self.assertEqual(kdb.get_run(self.db(), run_id)["status"], "running")  # activity resumes the run
            status, body = self.post("/api/runs/finish", {"outcome": "done", "summary": "architecture ready"}, sid)
            self.assertEqual((status, body["status"]), (200, "succeeded"), body)
            self.assertEqual(kdb.get_handoff(self.db(), handoff["id"])["status"], "done")
            self.assertEqual(self.post("/api/runs/finish", {"outcome": "failed", "summary": "x"}, sid)[1]["ok"],
                             False)  # no live run left: nothing to finish
            self.assertEqual(self.post("/api/runs/finish", {"outcome": "maybe", "summary": "x"}, sid)[0], 400)
            self.assertEqual(self.post("/api/runs/heartbeat", {}, sid)[1],
                             {"ok": False, "reason": "no_run", "run_id": None})
        finally:
            stream.close()

    def test_finish_requeues_coalesced_handoff_for_another_stage(self):
        sid = self.session_for()
        first = self.move("architect")
        run_id = self.claim(sid, handoff_id=first["id"])[1]["run_id"]
        again = self.move("architect")  # already there: same stage → no hand-off
        self.assertIsNone(again)
        self.move("discovery")
        coalesced = self.move("architect")  # target equals the live run's stage → coalesced
        self.assertEqual(coalesced["status"], "coalesced")
        conn = self.db()
        conn.execute("UPDATE runs SET stage='discovery' WHERE id=?", (run_id,))  # the run ended on another stage
        stream = Stream(self.d.port, self.d.client_token, self.pid)
        try:
            stream.next("hello")
            self.assertEqual(self.post("/api/runs/finish", {"outcome": "done", "summary": "ok"}, sid)[0], 200)
            ev = stream.next("handoff.created", where=lambda e: e["data"]["id"] == coalesced["id"])
            self.assertIsNotNone(ev, stream.seen)
            self.assertEqual(ev["data"]["status"], "requeued")
        finally:
            stream.close()
        self.assertEqual(kdb.get_handoff(conn, coalesced["id"])["status"], "requeued")

    def test_hook_activity_matching_and_sticky_terminal(self):
        start = kd.process_start_time(os.getpid())
        sid = self.session_for(pid=os.getpid(), start=start)
        run_id = self.claim(sid, handoff_id=self.move("architect")["id"])[1]["run_id"]
        other = [[1, "Thu Jan  1 00:00:00 1970"]]
        activity = "/api/hooks/activity"
        status, body = self.d.call("POST", activity, {"event": "Stop", "pids": other})
        self.assertEqual((status, body["matched"]), (200, 0))
        status, body = self.d.call("POST", activity, {"event": "Stop", "pids": [[os.getpid(), "wrong"]]})
        self.assertEqual(body["matched"], 0)  # pid reuse: the start time must match too
        status, body = self.d.call("POST", activity, {"event": "Stop", "pids": other + [[os.getpid(), start]]})
        self.assertEqual((body["matched"], kdb.get_run(self.db(), run_id)["status"]), (1, "waiting"))
        self.d.call("POST", activity, {"event": "PostToolUse", "pids": [[os.getpid(), start]]})
        self.assertEqual(kdb.get_run(self.db(), run_id)["status"], "running")
        self.post("/api/runs/finish", {"outcome": "done", "summary": "ok"}, sid)
        self.d.call("POST", activity, {"event": "Stop", "pids": [[os.getpid(), start]]})
        self.assertEqual(kdb.get_run(self.db(), run_id)["status"], "succeeded")
        self.assertEqual(self.d.call("POST", activity, {"event": "Stop", "pids": []}, token="ui")[0], 403)
        self.assertEqual(self.d.call("POST", activity, {"event": "Bogus", "pids": []})[0], 400)

    def test_dead_claude_process_abandons_the_run(self):
        sid = self.session_for(pid=2 ** 22 + 12345)  # no such process
        run_id = self.claim(sid, handoff_id=self.move("architect")["id"])[1]["run_id"]
        self.assertEqual(wait_for(lambda: kdb.get_run(self.db(), run_id)["status"] == "abandoned", 10), True)


class ListAndApprovalTests(RunsCase):
    def test_runs_list_live_and_view_state(self):
        sid = self.session_for(pid=os.getpid())
        run_id = self.claim(sid, handoff_id=self.move("architect")["id"])[1]["run_id"]
        self.claim(sid, ticket="signup")
        status, body = self.d.get("/api/runs?live=1")
        self.assertEqual(status, 200)
        mine = {r["ticket"]: r for r in body["runs"]}
        self.assertEqual(sorted(mine), ["login", "signup"])
        self.assertEqual((mine["login"]["id"], mine["login"]["view"], mine["login"]["project_name"]),
                         (run_id, "running", "proj"))
        self.db().execute("UPDATE runs SET heartbeat_at=heartbeat_at-3600 WHERE id=?", (run_id,))
        self.assertEqual({r["ticket"]: r["view"] for r in self.d.get("/api/runs?live=1")[1]["runs"]}["login"],
                         "stale")
        self.post("/api/runs/finish", {"outcome": "failed", "summary": "tests broke"}, sid)  # latest: signup
        live = [r["ticket"] for r in self.d.get("/api/runs?live=1")[1]["runs"]]
        self.assertEqual(live, ["login"])
        board = self.d.get(f"/api/projects/{self.pid}/runs")[1]
        by_ticket = {r["ticket"]: r for r in board["runs"]}
        self.assertEqual((by_ticket["signup"]["status"], by_ticket["signup"]["reason"]), ("failed", "tests broke"))
        self.assertEqual(self.d.get("/api/runs?live=1", token=None)[0], 401)
        self.assertEqual(self.d.get("/api/runs?live=1", token="ui")[0], 200)

    def test_approval_read(self):
        path = f"/api/projects/{self.pid}/tickets/login/approval"
        self.assertEqual(self.d.get(path)[1], {"recorded": False, "valid": False, "actor": "", "at": "",
                                               "hash12": "", "board_recorded": False})
        status, _ = self.d.call("POST", f"/api/projects/{self.pid}/tickets/login/approve", {}, token="ui")
        self.assertEqual(status, 200)
        body = self.d.get(path)[1]
        self.assertEqual((body["recorded"], body["valid"], body["actor"], body["board_recorded"], len(body["hash12"])),
                         (True, True, "human (board)", True, 12))
        (self.root / ".SDD/specs/login/01-discovery.md").write_text("# Discovery\n\nChanged.\n")
        self.assertEqual(self.d.get(path)[1]["valid"], False)
        self.assertEqual(self.d.get(f"/api/projects/{self.pid}/tickets/nope/approval")[0], 404)


class PickupTests(RunsCase):
    daemon_env = {"KANBAN_PICKUP_SECONDS": "1"}

    def test_pickup_timer_decides_headless_or_offer(self):
        stream = Stream(self.d.port, self.d.ui_token, self.pid)
        try:
            stream.next("hello")
            claimed = self.move("architect")
            self.assertEqual(self.claim(self.session_for(), handoff_id=claimed["id"])[1]["result"], "ok")
            waiting = self.move("architect", ticket="signup")
            ev = stream.next("handoff.pickup_timeout", timeout=8)
            self.assertIsNotNone(ev, stream.seen)
            self.assertEqual((ev["data"]["id"], ev["data"]["action"], ev["data"]["runner"]),
                             (waiting["id"], "headless", False))  # no channel session: auto headless requested
            # with a live channel session the card offers actions instead
            channel = self.session_for(channel=True)
            live = Stream(self.d.port, self.d.client_token, self.pid, session=channel)
            try:
                live.next("hello")
                offered = self.move("discovery", ticket="signup")
                ev = stream.next("handoff.pickup_timeout", timeout=8)
                self.assertEqual((ev["data"]["id"], ev["data"]["action"]), (offered["id"], "offer"))
            finally:
                live.close()
        finally:
            stream.close()
        timeouts = [e["data"]["id"] for e in stream.seen if e["event"] == "handoff.pickup_timeout"]
        self.assertNotIn(claimed["id"], timeouts)  # a claimed hand-off never times out
        board = self.d.get(f"/api/projects/{self.pid}/runs")[1]
        pending = {h["id"]: h for h in board["handoffs"]}
        self.assertEqual(list(pending), [offered["id"]])
        self.assertEqual(pending[offered["id"]]["pickup"], "offer")
        self.assertIn('kanban_start with ticket "signup"', pending[offered["id"]]["prompt"])
        status, body = self.d.call("POST", f"/api/projects/{self.pid}/tickets/signup/runs/start", {}, token="ui")
        self.assertEqual((status, body["error"]), (501, "runner not installed"))

    def test_live_non_channel_session_gets_an_offer(self):
        """B8: any live interactive session of the project (channel or not) → offer, never an auto headless run."""
        stream = Stream(self.d.port, self.d.ui_token, self.pid)
        plain = self.session_for(channel=False)
        live = Stream(self.d.port, self.d.client_token, self.pid, session=plain)
        try:
            stream.next("hello")
            live.next("hello")
            h = self.move("architect")
            ev = stream.next("handoff.pickup_timeout", timeout=8)
            self.assertIsNotNone(ev, stream.seen)
            self.assertEqual((ev["data"]["id"], ev["data"]["action"]), (h["id"], "offer"))
        finally:
            live.close()
            stream.close()

    def test_requeued_handoff_is_re_evaluated(self):
        """B7: a requeued hand-off gets a fresh pickup decision: with no live session, auto headless."""
        conn = self.db()
        run = kdb.create_run(conn, self.pid, "login", "qa", "interactive", status="running")
        h = kdb.create_handoff(conn, self.pid, "login", "start", "developer", "qa", "human (board)")
        self.assertEqual(h["status"], "coalesced")
        conn.execute("UPDATE runs SET stage='developer' WHERE id=?", (run["id"],))  # the run moved on (a claim)
        stream = Stream(self.d.port, self.d.ui_token, self.pid)
        try:
            stream.next("hello")
            kdb.transition_run(conn, run["id"], "succeeded")
            self.assertEqual(kdb.get_handoff(conn, h["id"])["status"], "requeued")
            ev = stream.next("handoff.pickup_timeout", timeout=8, where=lambda e: e["data"]["id"] == h["id"])
            self.assertIsNotNone(ev, stream.seen)
            self.assertEqual(ev["data"]["action"], "headless")
        finally:
            stream.close()
        pending = {x["id"]: x for x in self.d.get(f"/api/projects/{self.pid}/runs")[1]["handoffs"]}
        self.assertEqual(pending[h["id"]]["pickup"], "headless")


class PickupDecisionTests(unittest.TestCase):
    def test_decisions_pruned_and_requeue_re_evaluated(self):
        """B7: decisions of hand-offs no longer pending are pruned; a requeue drops the old decision."""
        import routes_runs
        with routes_runs._pickup_lock:
            saved = dict(routes_runs._pickup)
            routes_runs._pickup.clear()
            routes_runs._pickup.update({"h-claimed": ("queued", "offer"), "h-wait": ("delivered", "offer"),
                                        "h-again": ("queued", "offer"), "h-re": ("requeued", "headless")})
        try:
            routes_runs.reconcile_pickup([{"id": "h-wait", "status": "delivered"},
                                          {"id": "h-again", "status": "requeued"},
                                          {"id": "h-re", "status": "requeued"}])
            with routes_runs._pickup_lock:
                self.assertEqual(routes_runs._pickup, {"h-wait": ("delivered", "offer"),
                                                       "h-re": ("requeued", "headless")})
        finally:
            with routes_runs._pickup_lock:
                routes_runs._pickup.clear()
                routes_runs._pickup.update(saved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
