#!/usr/bin/env python3
"""Operational store (kanban_db): migrations, run states, hand-off accessors, approvals (stdlib only)."""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
import unittest
from pathlib import Path

import helpers  # noqa: F401  (isolates KANBAN_HOME before the store is imported)
import kanban_db as kdb
import kanban_md as km

TABLES = ["approval_files", "approvals", "handoffs", "projects", "run_events", "runs", "sessions", "settings",
          "ticket_sessions"]


class DbBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kanban-db-"))
        self.home = self.tmp / "home"
        self.root = helpers.make_project(self.tmp)
        self.store = kdb.Store(kdb.db_path(kdb.prepare_home(self.home)))
        self.conn = self.store.conn()
        self.pid = kdb.register_project(self.conn, self.root)["id"]

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class MigrationTests(DbBase):
    def test_db_migrates_and_recovers(self):
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        names = [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        self.assertEqual(names, TABLES)
        index = self.conn.execute("SELECT sql FROM sqlite_master WHERE name='runs_one_live_per_ticket'").fetchone()[0]
        self.assertIn("UNIQUE INDEX", index)
        self.assertIn("WHERE status IN ('queued', 'running', 'waiting')", index)
        self.assertEqual(stat.S_IMODE(os.stat(self.home).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(kdb.db_path(self.home)).st_mode), 0o600)
        # the project id is derived from the path, so it survives a deleted DB
        km.create_ticket(self.root, "Login")
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(kdb.db_path(self.home)) + suffix).unlink(missing_ok=True)
        store = kdb.Store(kdb.db_path(self.home))
        try:
            conn = store.conn()
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(kdb.register_project(conn, self.root)["id"], self.pid)
            self.assertEqual([t["id"] for t in km.list_tickets(self.root)], ["login"])  # board = markdown
        finally:
            store.close()

    def test_newer_schema_is_refused(self):
        self.conn.execute("PRAGMA user_version = 7")
        with self.assertRaises(RuntimeError):
            kdb.migrate(self.conn)

    def test_connection_per_thread(self):
        seen = []
        t = threading.Thread(target=lambda: seen.append(self.store.conn()))
        t.start()
        t.join()
        self.assertIsNot(seen[0], self.conn)
        self.assertIs(self.store.conn(), self.conn)

    def test_project_registry(self):
        again = kdb.register_project(self.conn, str(self.root))
        self.assertEqual(again["id"], self.pid)
        self.assertEqual(kdb.get_project(self.conn, self.pid)["root"], str(self.root))
        self.assertIsNone(kdb.get_project(self.conn, "nope"))
        with self.assertRaises(ValueError):
            kdb.register_project(self.conn, self.tmp / "missing")
        with self.assertRaises(ValueError):
            kdb.register_project(self.conn, "relative/path")

    def test_settings(self):
        settings = kdb.Settings(self.store)
        self.assertEqual(settings.get("pickup_seconds", "45"), "45")
        settings.set("pickup_seconds", 30)
        self.assertEqual(settings.get("pickup_seconds"), "30")


class RunTests(DbBase):
    def test_run_terminal_states_sticky(self):
        run = kdb.create_run(self.conn, self.pid, "login", "developer", "headless")
        self.assertEqual(run["status"], "queued")
        kdb.transition_run(self.conn, run["id"], "running")
        kdb.transition_run(self.conn, run["id"], "waiting")
        kdb.transition_run(self.conn, run["id"], "running")
        done = kdb.transition_run(self.conn, run["id"], "succeeded", reason="finished")
        self.assertIsNotNone(done["ended_at"])
        for status in ("waiting", "running", "queued", "failed", "cancelled", "abandoned"):
            with self.assertRaises(ValueError):
                kdb.transition_run(self.conn, run["id"], status)
        self.assertEqual(kdb.get_run(self.conn, run["id"])["status"], "succeeded")
        with self.assertRaises(ValueError):
            kdb.transition_run(self.conn, run["id"], "bogus")
        events = [(e["type"], e["data"]["to"]) for e in kdb.run_events(self.conn, run["id"])]
        self.assertEqual(events, [("status", "queued"), ("status", "running"), ("status", "waiting"),
                                  ("status", "running"), ("status", "succeeded")])

    def test_transition_table(self):
        self.assertTrue(kdb.run_transition_allowed("queued", "running"))
        self.assertFalse(kdb.run_transition_allowed("queued", "waiting"))
        self.assertFalse(kdb.run_transition_allowed("succeeded", "waiting"))
        self.assertFalse(kdb.run_transition_allowed("abandoned", "running"))

    def test_one_live_run_per_ticket(self):
        run = kdb.create_run(self.conn, self.pid, "login", "developer", "interactive", status="running")
        with self.assertRaises(kdb.LiveRunExists):
            kdb.create_run(self.conn, self.pid, "login", "qa", "headless")
        kdb.create_run(self.conn, self.pid, "other", "qa", "headless")  # other ticket is fine
        kdb.transition_run(self.conn, run["id"], "failed")
        self.assertIsNone(kdb.live_run(self.conn, self.pid, "login"))
        kdb.create_run(self.conn, self.pid, "login", "qa", "headless")  # the old one is terminal
        self.assertEqual(len(kdb.live_runs(self.conn, self.pid)), 2)


class HandoffTests(DbBase):
    def test_supersede_coalesce_requeue(self):
        first = kdb.create_handoff(self.conn, self.pid, "login", "start", "discovery", "architect", "human (board)")
        self.assertEqual((first["status"], first["superseded"]), ("queued", []))
        second = kdb.create_handoff(self.conn, self.pid, "login", "rework", "architect", "discovery", "human (board)")
        self.assertEqual(second["superseded"], [first["id"]])
        self.assertEqual(kdb.get_handoff(self.conn, first["id"])["status"], "superseded")
        self.assertEqual(kdb.claim(self.conn, first["id"]), ("superseded", None))
        status, run_id = kdb.claim(self.conn, second["id"], session_id="s1")
        self.assertEqual(status, "ok")
        self.assertEqual(kdb.claim(self.conn, second["id"], run_id=run_id), ("ok", run_id))  # idempotent
        self.assertEqual(kdb.claim(self.conn, second["id"]), ("already_claimed", None))
        self.assertEqual(kdb.get_run(self.conn, run_id)["stage"], "discovery")
        # a move to the live run's stage coalesces into it; another target stays queued and is not superseded by it
        same = kdb.create_handoff(self.conn, self.pid, "login", "start", "architect", "discovery", "human (board)")
        self.assertEqual((same["status"], same["run_id"]), ("coalesced", run_id))
        self.assertEqual(kdb.claim(self.conn, same["id"]), ("already_claimed", None))
        # the run switches stage (e.g. re-claimed for another stage); on finish the coalesced one is requeued
        self.conn.execute("UPDATE runs SET stage='architect' WHERE id=?", (run_id,))
        kdb.transition_run(self.conn, run_id, "succeeded")
        self.assertEqual(kdb.get_handoff(self.conn, same["id"])["status"], "requeued")
        self.assertEqual(kdb.claim(self.conn, same["id"])[0], "ok")
        self.assertEqual(kdb.claim(self.conn, "missing"), ("not_found", None))

    def test_coalesced_same_stage_is_done_when_run_finishes(self):
        run = kdb.create_run(self.conn, self.pid, "login", "architect", "interactive", status="running")
        h = kdb.create_handoff(self.conn, self.pid, "login", "start", "discovery", "architect", "human (board)")
        self.assertEqual(h["status"], "coalesced")
        kdb.transition_run(self.conn, run["id"], "succeeded")
        self.assertEqual(kdb.get_handoff(self.conn, h["id"])["status"], "done")

    def test_claim_binds_a_prepared_headless_run(self):
        h = kdb.create_handoff(self.conn, self.pid, "login", "start", "discovery", "architect", "human (board)")
        run = kdb.create_run(self.conn, self.pid, "login", "architect", "headless", handoff_id=h["id"])
        self.assertEqual(kdb.claim(self.conn, h["id"], run_id=run["id"]), ("ok", run["id"]))
        self.assertEqual(kdb.get_run(self.conn, run["id"])["status"], "running")
        self.assertEqual(kdb.claim(self.conn, h["id"], run_id=run["id"]), ("ok", run["id"]))

    def test_delivered_and_pending(self):
        h = kdb.create_handoff(self.conn, self.pid, "login", "start", "discovery", "architect", "human (board)")
        kdb.mark_delivered(self.conn, h["id"])
        self.assertEqual(kdb.get_handoff(self.conn, h["id"])["status"], "delivered")
        self.assertEqual([p["id"] for p in kdb.pending_handoffs(self.conn, self.pid)], [h["id"]])
        with self.assertRaises(ValueError):
            kdb.create_handoff(self.conn, self.pid, "login", "sideways", "discovery", "architect", "human (board)")


class ApprovalTests(DbBase):
    def test_record_approval_writes_markdown_row_and_snapshot(self):
        km.create_ticket(self.root, "Login")
        folder = self.root / ".SDD/specs/login"
        (folder / "03-architecture.md").write_text("# Architecture\n\n| a | b |\n|---|---|\n\nBody.\n")
        rec = kdb.record_approval(self.conn, self.root, self.pid, "login", "human (board)", session_id=None)
        self.assertEqual(rec["actor"], "human (board)")
        self.assertIn("approved_by: human (board)", (folder / "README.md").read_text())
        self.assertIn("Approved for execution by human (board)", (folder / "03-architecture.md").read_text())
        latest = kdb.latest_approval(self.conn, self.pid, "login")
        self.assertEqual((latest["actor"], latest["hash"]), ("human (board)", km.spec_hash(folder)))
        files = kdb.approval_files(self.conn, latest["id"])
        self.assertEqual(sorted(files), ["03-architecture.md"])
        self.assertIn("Body.", files["03-architecture.md"])
        with self.assertRaises(ValueError):
            kdb.record_approval(self.conn, self.root, self.pid, "login", "someone")
        with self.assertRaises(KeyError):
            kdb.record_approval(self.conn, self.root, self.pid, "missing", "human (chat)")

    def test_ticket_sessions(self):
        kdb.set_ticket_session(self.conn, self.pid, "login", "abc-123")
        kdb.set_ticket_session(self.conn, self.pid, "login", "def-456")
        self.assertEqual(kdb.get_ticket_session(self.conn, self.pid, "login"), "def-456")
        self.assertIsNone(kdb.get_ticket_session(self.conn, self.pid, "other"))


class SessionTests(DbBase):
    def test_register_session(self):
        s = kdb.register_session(self.conn, self.pid, "headless", 123, "Mon Sep 28 10:00:00 2026", channel=False,
                                 run_id="r-1")
        got = kdb.get_session(self.conn, s["id"])
        self.assertEqual((got["kind"], got["channel"], got["claude_pid"], got["run_id"]), ("headless", 0, 123, "r-1"))
        with self.assertRaises(ValueError):
            kdb.register_session(self.conn, self.pid, "robot", 1, "")



class ProcessHelperTests(unittest.TestCase):
    def test_one_process_helper(self):
        """B10: kanban_db holds the only `ps` start-time / liveness helper; daemon, runner and routes_runs reuse it."""
        import subprocess
        import daemon as kd
        import routes_runs
        import runner
        self.assertIs(kd.process_start_time, kdb.process_start_time)
        for module in (runner, routes_runs):
            self.assertFalse(hasattr(module, "_start_time"), module.__name__)
            self.assertFalse(hasattr(module, "_alive"), module.__name__)
        self.assertFalse(hasattr(kd, "_alive"))
        for script in helpers.SCRIPTS.glob("*.py"):
            if script.name not in ("kanban_db.py", "hook.py"):  # hook.py stays import-light (its own ppid walk)
                self.assertNotIn('"lstart="', script.read_text(), script.name)
        me = os.getpid()
        start = kdb.process_start_time(me)
        self.assertTrue(start)
        self.assertTrue(kdb.process_alive(me))
        self.assertTrue(kdb.process_alive(me, start))
        self.assertTrue(kdb.process_alive(me, "  " + start.replace(" ", "  ")))  # whitespace-insensitive
        self.assertFalse(kdb.process_alive(me, "Thu Jan  1 00:00:00 1970"))  # a reused pid is not the same process
        gone = subprocess.Popen(["true"])
        gone.wait()
        self.assertFalse(kdb.process_alive(gone.pid))
        self.assertEqual(kdb.process_start_time(gone.pid), "")
        for bad in (None, "x", 0, -1):
            self.assertFalse(kdb.process_alive(bad))
            self.assertEqual(kdb.process_start_time(bad), "")

if __name__ == "__main__":
    unittest.main(verbosity=2)
