#!/usr/bin/env python3
"""runner.py (PRD-04): headless `claude -p` runs, their transcript and Stop, against a throwaway daemon in a temp
KANBAN_HOME. The real `claude` is never run: KANBAN_CLAUDE_BIN points at tests/fixtures/fake_claude.py, whose
behaviour comes from `.fake-claude.json` in the project root (the child only gets an allow-listed environment)."""
from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path

import helpers
import kanban_db as kdb
import kanban_md as km
import kanban_rules as rules
import runner


def _fake_binary() -> str:
    """fixtures/fake_claude.py with this interpreter in its shebang: `#!/usr/bin/env python3` could go through a
    version-manager shim that adds its own variables, which would blur the environment assertions."""
    folder = Path(tempfile.mkdtemp(prefix="kanban-fake-claude-"))
    atexit.register(shutil.rmtree, str(folder), True)
    source = (helpers.FIXTURES / "fake_claude.py").read_text().split("\n", 1)[1]
    target = folder / "claude"
    target.write_text(f"#!{sys.executable}\n{source}")
    target.chmod(0o755)
    return str(target)


FAKE = _fake_binary()
ARGV_TAIL = ["--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits"]
ALLOWED = {"HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "SHELL", "PATH", "KANBAN_HOME", "CLAUDE_PROJECT_DIR",
           "KANBAN_RUN_ID", "KANBAN_HANDOFF_ID"}


def wait_for(predicate, timeout=10.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    try:  # a zombie still answers kill(0); ps shows its state as Z
        import subprocess
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return out == "" or out.startswith("Z")
    except OSError:
        return False


# ---------------------------------------------------------------- pure parts
class UnitTests(unittest.TestCase):
    def test_argv_exact(self):
        self.assertEqual(runner.build_argv("/bin/claude", "PROMPT"),
                         ["/bin/claude", "-p", "PROMPT", "--output-format", "stream-json", "--verbose",
                          "--permission-mode", "acceptEdits"])
        self.assertEqual(runner.build_argv("/bin/claude", "PROMPT", resume="sess-1"),
                         ["/bin/claude", "-p", "PROMPT", "--output-format", "stream-json", "--verbose",
                          "--permission-mode", "acceptEdits", "--resume", "sess-1"])
        self.assertNotIn("--dangerously-skip-permissions", runner.build_argv("c", "p", resume="s"))

    def test_child_env_allowlist_unit(self):
        base = {"HOME": "/h", "USER": "u", "LOGNAME": "u", "LANG": "en_US.UTF-8", "LC_CTYPE": "UTF-8",
                "TMPDIR": "/t", "SHELL": "/bin/zsh", "PATH": "/daemon/path", "CLAUDECODE": "1",
                "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PROJECT_DIR": "/elsewhere", "AWS_SECRET_ACCESS_KEY": "x",
                "KANBAN_RUN_ID": "r-parent"}
        env = runner.child_env(base, root="/proj", run_id="r-1", handoff_id="h-1", path="/resolved:/bin",
                               home="/kh")
        self.assertEqual(env, {"HOME": "/h", "USER": "u", "LOGNAME": "u", "LANG": "en_US.UTF-8", "LC_CTYPE": "UTF-8",
                               "TMPDIR": "/t", "SHELL": "/bin/zsh", "PATH": "/resolved:/bin", "KANBAN_HOME": "/kh",
                               "CLAUDE_PROJECT_DIR": "/proj", "KANBAN_RUN_ID": "r-1", "KANBAN_HANDOFF_ID": "h-1"})

    def test_resolve_claude_override(self):
        self.assertEqual(runner.resolve_claude({"KANBAN_CLAUDE_BIN": FAKE}), FAKE)
        self.assertIsNone(runner.resolve_claude({"KANBAN_CLAUDE_BIN": "/nonexistent/claude"}))

    def test_parse_stream_lines(self):
        init = runner.parse_line(json.dumps({"type": "system", "subtype": "init", "session_id": "s-9"}))
        self.assertEqual(init["session_id"], "s-9")
        self.assertEqual([e[0] for e in init["events"]], ["system"])
        msg = runner.parse_line(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "<b>hi</b>"}, {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}))
        self.assertEqual(msg["events"][0], ("assistant", "<b>hi</b>"))
        self.assertEqual(msg["events"][1][0], "tool_use")
        self.assertIn("Bash", msg["events"][1][1])
        res = runner.parse_line(json.dumps({"type": "result", "subtype": "success", "result": "ok",
                                            "permission_denials": [{"tool_name": "Write", "tool_input": {"a": 1}}]}))
        self.assertEqual([e[0] for e in res["events"]], ["result", "permission_denial"])
        self.assertEqual(res["denials"], 1)
        self.assertEqual(runner.parse_line("not json")["events"], [("output", "not json")])
        self.assertEqual(runner.parse_line("   ")["events"], [])


# ---------------------------------------------------------------- against a daemon
class RunnerCase(unittest.TestCase):
    daemon_env: dict = {}
    scenario: dict = {}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kanban-runner-"))
        self.root = helpers.make_project(self.tmp)
        for title in ("Login", "Signup"):
            km.create_ticket(self.root, title)
        self.set_scenario(self.scenario)
        self.home = helpers.temp_home()
        self.daemons = []
        self.d = self.start_daemon()
        self.pid = self.d.register(self.root)["project_id"]
        self.store = kdb.Store(kdb.db_path(self.home))

    def start_daemon(self):
        env = {"KANBAN_CLAUDE_BIN": FAKE, "KANBAN_SWEEP_SECONDS": "0.2", "KANBAN_PICKUP_SECONDS": "3600",
               **self.daemon_env}
        d = helpers.TestDaemon(home=self.home, **env).start()
        self.daemons.append(d)
        return d

    def tearDown(self):
        out = self.root / ".fake-claude"
        out.mkdir(exist_ok=True)
        (out / "release").write_text("")
        self.store.close()
        for d in self.daemons:
            d.stop()
        for pidfile in out.glob("*-grandchild.pid"):
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    # helpers
    def set_scenario(self, scenario):
        (self.root / ".fake-claude.json").write_text(json.dumps(scenario))

    def db(self):
        return self.store.conn()

    def move(self, stage="architect", ticket="login"):
        status, body = self.d.call("POST", f"/api/projects/{self.pid}/tickets/{ticket}/move", {"stage": stage},
                                   token="ui")
        self.assertEqual(status, 200, body)
        return body["handoff"]

    def start(self, ticket="login", handoff_id=None, expect=200):
        status, body = self.d.call("POST", f"/api/projects/{self.pid}/tickets/{ticket}/runs/start",
                                   {"handoff_id": handoff_id} if handoff_id else {}, token="ui")
        self.assertEqual(status, expect, body)
        return body.get("run") if expect == 200 else body

    def run_row(self, run_id):
        return kdb.get_run(self.db(), run_id)

    def wait_status(self, run_id, *statuses, timeout=10.0):
        row = wait_for(lambda: (self.run_row(run_id) or {}).get("status") in statuses and self.run_row(run_id),
                       timeout=timeout)
        self.assertIn((self.run_row(run_id) or {}).get("status"), statuses, self.d.log_text())
        return row

    def dumps(self, run_id):
        files = sorted((self.root / ".fake-claude").glob(f"{run_id}-[0-9]*.json"),
                       key=lambda p: int(p.stem.rsplit("-", 1)[1]))
        return [json.loads(p.read_text()) for p in files]

    def release(self, run_id):
        """Let a waiting fake finish (after it has started, so its output folder exists)."""
        self.assertTrue(wait_for(lambda: self.dumps(run_id)), self.d.log_text())
        (self.root / ".fake-claude" / f"release-{run_id}").write_text("")

    def events(self, run_id, after=0, limit=None):
        path = f"/api/runs/{run_id}/events?afterSeq={after}" + (f"&limit={limit}" if limit else "")
        status, body = self.d.get(path, token="ui")
        self.assertEqual(status, 200, body)
        return body


class RunTests(RunnerCase):
    scenario = {"session_id": "sess-1"}

    def test_argv_exact(self):
        h = self.move()
        run = self.start(handoff_id=h["id"])
        self.assertEqual(run["kind"], "headless")
        self.wait_status(run["id"], "succeeded")
        first = self.dumps(run["id"])[0]
        prompt = rules.headless_prompt({**h, "handoff_id": h["id"]})
        self.assertEqual(first["argv"][1:], ["-p", prompt, *ARGV_TAIL])
        self.assertEqual(Path(first["argv"][0]).resolve(), Path(FAKE).resolve())
        self.assertEqual(Path(first["cwd"]).resolve(), self.root)
        self.assertNotEqual(first["pgid"], os.getpgid(0))  # own process group (start_new_session)
        self.assertEqual(first["pid"], first["pgid"])
        self.assertEqual(kdb.get_handoff(self.db(), h["id"])["status"], "done")
        # a stored ticket session is resumed next time
        h2 = self.move("discovery")
        run2 = self.start(handoff_id=h2["id"])
        self.wait_status(run2["id"], "succeeded")
        argv = self.dumps(run2["id"])[0]["argv"]
        self.assertEqual(argv[1:], ["-p", rules.headless_prompt({**h2, "handoff_id": h2["id"]}), *ARGV_TAIL,
                                    "--resume", "sess-1"])
        self.assertNotIn("--dangerously-skip-permissions", argv)

    def test_stream_json_to_events_and_session(self):
        run = self.start(handoff_id=self.move()["id"])
        self.wait_status(run["id"], "succeeded")
        body = self.events(run["id"])
        kinds = [e["kind"] for e in body["events"] if e["kind"] != "status"]
        self.assertEqual(kinds, ["system", "assistant", "tool_use", "tool_result", "result", "exit"])
        seqs = [e["seq"] for e in body["events"]]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))
        statuses = [e["text"] for e in body["events"] if e["kind"] == "status"]
        self.assertEqual(statuses[0], "queued")
        self.assertTrue(statuses[-1].startswith("running → succeeded"), statuses)
        self.assertEqual(kdb.get_ticket_session(self.db(), self.pid, "login"), "sess-1")
        self.assertEqual(body["run"]["status"], "succeeded")
        # paging by afterSeq
        page = self.events(run["id"], after=seqs[0], limit=2)
        self.assertEqual([e["seq"] for e in page["events"]], seqs[1:3])
        self.assertTrue(page["more"])
        self.assertEqual(page["last_seq"], seqs[2])
        self.assertEqual(self.events(run["id"], after=seqs[-1])["events"], [])
        self.assertEqual(self.d.get("/api/runs/r-nope/events", token="ui")[0], 404)

    def test_exit_code_and_finish(self):
        self.set_scenario({"exit": 3})
        run = self.start(handoff_id=self.move()["id"])
        row = self.wait_status(run["id"], "failed")
        self.assertIn("exited with code 3", row["reason"])
        # Claude already called kanban_finish(failed): exit 0 does not turn it into succeeded
        self.set_scenario({"finish": "failed"})
        run = self.start(handoff_id=self.move("discovery")["id"])
        row = self.wait_status(run["id"], "failed")
        time.sleep(0.3)
        self.assertEqual(self.run_row(run["id"])["status"], "failed")
        self.assertEqual(self.run_row(run["id"])["reason"], "finished by the fake")

    def test_start_refusals(self):
        self.assertEqual(self.start(expect=409)["error"], "no pending hand-off for this ticket")
        self.set_scenario({"wait": True})
        run = self.start(handoff_id=self.move()["id"])
        self.wait_status(run["id"], "running")
        again = self.start(handoff_id=self.move("discovery")["id"], expect=409)  # one live run per ticket
        self.assertIn("live run", again["error"])
        self.assertEqual(self.d.call("POST", f"/api/projects/{self.pid}/tickets/login/runs/start", {},
                                     token="client")[0], 403)
        self.assertEqual(self.d.call("POST", f"/api/runs/{run['id']}/stop", {}, token="client")[0], 403)


class NeedsInputTests(RunnerCase):
    def card(self, ticket="login"):
        runs = self.d.get(f"/api/projects/{self.pid}/runs", token="ui")[1]["runs"]
        return {r["ticket"]: r for r in runs}[ticket]

    def ticket_run(self, run_id, ticket="login"):
        runs = self.d.get(f"/api/projects/{self.pid}/tickets/{ticket}/runs", token="ui")[1]["runs"]
        return {r["id"]: r for r in runs}[run_id]

    def test_needs_input_flag_cleared_by_move(self):
        self.set_scenario({"finish": "needs_input"})
        run = self.start(handoff_id=self.move()["id"])
        row = self.wait_status(run["id"], "succeeded")
        self.assertEqual(row["reason"], "finished by the fake")
        card = self.card()
        self.assertEqual((card["id"], card["needs_input"], card["needs_input_summary"]),
                         (run["id"], True, "finished by the fake"))
        kinds = [e["kind"] for e in self.events(run["id"])["events"]]
        self.assertIn("needs_input", kinds)
        self.move("discovery")  # a human move of the ticket clears the flag
        card = self.ticket_run(run["id"])
        self.assertEqual((card["needs_input"], card["needs_input_summary"]), (False, None))

    def test_needs_input_flag_cleared_by_new_run(self):
        self.set_scenario({"wait": True, "finish": "needs_input"})
        first = self.start(handoff_id=self.move()["id"])
        self.wait_status(first["id"], "running")
        second_handoff = self.move("discovery")  # queued behind the live run; this move precedes the flag
        self.release(first["id"])
        self.wait_status(first["id"], "succeeded")
        self.assertTrue(self.ticket_run(first["id"])["needs_input"])
        self.set_scenario({})
        second = self.start(handoff_id=second_handoff["id"])
        self.assertFalse(self.ticket_run(first["id"])["needs_input"])
        self.wait_status(second["id"], "succeeded")
        self.assertFalse(self.card()["needs_input"])
        status, _, js = helpers.request(self.d.port, "GET", "/ui/modules/runs.js")
        self.assertIn(b"needs input: ", js)
        self.assertIn(b"needs_input_summary", js)

    def test_plain_success_has_no_flag(self):
        run = self.start(handoff_id=self.move()["id"])
        self.wait_status(run["id"], "succeeded")
        self.assertEqual((self.card()["needs_input"], self.card()["needs_input_summary"]), (False, None))


class EnvTests(RunnerCase):
    daemon_env = {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PROJECT_DIR": "/somewhere/else",
                  "SECRET_API_TOKEN": "do-not-pass", "LC_CTYPE": "UTF-8"}

    def test_child_env_allowlist(self):
        h = self.move()
        run = self.start(handoff_id=h["id"])
        self.wait_status(run["id"], "succeeded")
        env = self.dumps(run["id"])[0]["env"]
        extra = {k for k in env if k not in ALLOWED and not k.startswith("LC_") and not k.startswith("__CF")}
        self.assertEqual(extra, set(), env)
        self.assertEqual(env["CLAUDE_PROJECT_DIR"], str(self.root))
        self.assertEqual(env["KANBAN_RUN_ID"], run["id"])
        self.assertEqual(env["KANBAN_HANDOFF_ID"], h["id"])
        self.assertEqual(Path(env["KANBAN_HOME"]).resolve(), self.home.resolve())
        self.assertEqual(env["LC_CTYPE"], "UTF-8")
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("SECRET_API_TOKEN", env)
        self.assertEqual([k for k in env if k.startswith("CLAUDE")], ["CLAUDE_PROJECT_DIR"])


class ResumeTests(RunnerCase):
    scenario = {"resume_fail": True, "session_id": "sess-new"}

    def test_resume_fallback(self):
        kdb.set_ticket_session(self.db(), self.pid, "login", "sess-old")
        run = self.start(handoff_id=self.move()["id"])
        self.wait_status(run["id"], "succeeded")
        attempts = self.dumps(run["id"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["argv"][-2:], ["--resume", "sess-old"])
        self.assertNotIn("--resume", attempts[1]["argv"])
        self.assertEqual(kdb.get_ticket_session(self.db(), self.pid, "login"), "sess-new")
        texts = [e["text"] for e in self.events(run["id"])["events"] if e["kind"] == "runner"]
        self.assertTrue(any("without --resume" in t for t in texts), texts)


class DenialTests(RunnerCase):
    scenario = {"denials": [{"tool_name": "Bash", "tool_use_id": "tu9", "tool_input": {"command": "rm -rf build"}}]}

    def test_permission_denials_badge(self):
        run = self.start(handoff_id=self.move()["id"])
        self.wait_status(run["id"], "succeeded")
        denied = [e for e in self.events(run["id"])["events"] if e["kind"] == "permission_denial"]
        self.assertEqual(len(denied), 1)
        self.assertIn("Bash", denied[0]["text"])
        self.assertIn("rm -rf build", denied[0]["text"])
        card = {r["ticket"]: r for r in self.d.get(f"/api/projects/{self.pid}/runs", token="ui")[1]["runs"]}
        self.assertEqual(card["login"]["permission_denials"], 1)
        status, _, js = helpers.request(self.d.port, "GET", "/ui/modules/runs.js")
        self.assertEqual(status, 200)
        self.assertIn(b"permission_denials", js)


class StopTests(RunnerCase):
    scenario = {"wait": True, "grandchild": True}

    def grandchild(self, run_id):
        path = self.root / ".fake-claude" / f"{run_id}-grandchild.pid"
        wait_for(path.exists)
        return int(path.read_text())

    def test_stop_kills_group(self):
        run = self.start(handoff_id=self.move()["id"])
        self.wait_status(run["id"], "running")
        grand = self.grandchild(run["id"])
        child = self.dumps(run["id"])[0]["pid"]
        started = time.time()
        status, body = self.d.call("POST", f"/api/runs/{run['id']}/stop", {}, token="ui")
        self.assertEqual((status, body["run"]["status"]), (200, "cancelled"), body)
        self.assertTrue(wait_for(lambda: gone(child) and gone(grand), timeout=8), (child, grand))
        self.assertLess(time.time() - started, 5.0)  # SIGTERM was enough: no 5 s grace needed
        self.assertEqual(self.run_row(run["id"])["status"], "cancelled")
        time.sleep(0.3)
        self.assertEqual(self.run_row(run["id"])["status"], "cancelled")  # the exit does not overwrite it
        self.assertEqual(self.d.call("POST", f"/api/runs/{run['id']}/stop", {}, token="ui")[0], 409)
        self.assertEqual(self.d.call("POST", "/api/runs/r-nope/stop", {}, token="ui")[0], 404)

    def test_stop_escalates_to_sigkill(self):
        self.set_scenario({"wait": True, "ignore_term": True})
        run = self.start(handoff_id=self.move()["id"])
        self.wait_status(run["id"], "running")
        wait_for(lambda: self.dumps(run["id"]))
        child = self.dumps(run["id"])[0]["pid"]
        wait_for(lambda: (self.root / ".fake-claude" / f"{run['id']}-term-ignored").exists())
        status, body = self.d.call("POST", f"/api/runs/{run['id']}/stop", {}, token="ui")
        self.assertEqual((status, body["run"]["status"]), (200, "cancelled"))
        self.assertTrue(wait_for(lambda: gone(child), timeout=10))


class OrphanTests(RunnerCase):
    scenario = {"wait": True}

    def test_orphans_on_restart(self):
        run = self.start(handoff_id=self.move()["id"])
        self.wait_status(run["id"], "running")
        self.d.proc.kill()  # no clean shutdown
        self.d.proc.wait(5)
        (self.home / "daemon.json").unlink()
        self.d = self.start_daemon()
        row = self.wait_status(run["id"], "failed")
        self.assertIn("restarted", row["reason"])

    def test_retention_sweep(self):
        conn = self.db()
        run = kdb.create_run(conn, self.pid, "signup", "architect", "headless", status="running")
        kdb.transition_run(conn, run["id"], "failed", reason="old")
        old = time.time() - 31 * 86400
        conn.execute("UPDATE run_events SET at=? WHERE run_id=?", (old, run["id"]))
        conn.execute("INSERT INTO run_events (run_id, at, type, data) VALUES (?, ?, 'transcript', '{}')",
                     (run["id"], time.time()))
        self.d.stop()
        self.d = self.start_daemon()
        left = wait_for(lambda: conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id=?",
                                             (run["id"],)).fetchone()[0] == 1, timeout=5)
        self.assertTrue(left)
        self.assertEqual(conn.execute("SELECT MIN(at) FROM run_events WHERE run_id=?", (run["id"],)).fetchone()[0]
                         > old, True)


class ConcurrencyTests(RunnerCase):
    scenario = {"wait": True}

    def test_concurrency_cap(self):
        tickets = []
        for n in range(1, 6):
            tickets.append(km.create_ticket(self.root, f"Task {n}")["id"])
        runs = []
        for ticket in tickets:
            runs.append(self.start(ticket=ticket, handoff_id=self.move(ticket=ticket)["id"]))
        for run in runs[:4]:
            self.wait_status(run["id"], "running")
        self.assertEqual(self.run_row(runs[4]["id"])["status"], "queued")  # KANBAN_MAX_RUNS defaults to 4
        time.sleep(0.5)
        self.assertEqual(self.run_row(runs[4]["id"])["status"], "queued")
        self.release(runs[0]["id"])
        self.wait_status(runs[0]["id"], "succeeded")
        self.wait_status(runs[4]["id"], "running")  # the freed slot starts the queued run


class MissingClaudeTests(RunnerCase):
    daemon_env = {"KANBAN_CLAUDE_BIN": "/nonexistent/claude"}

    def test_missing_claude_fails_clearly(self):
        h = self.move()
        run = self.start(handoff_id=h["id"])
        self.assertEqual(run["status"], "failed")
        self.assertIn("claude CLI not found", run["reason"])
        self.assertIn(kdb.get_handoff(self.db(), h["id"])["status"], ("queued", "delivered"))  # still claimable


class TranscriptTests(RunnerCase):
    scenario = {"text": "<img src=x onerror=alert(1)>"}

    def test_transcript_escapes_html(self):
        for name in ("transcript.js", "runs.js"):
            status, headers, js = helpers.request(self.d.port, "GET", f"/ui/modules/{name}")
            self.assertEqual(status, 200, name)
            self.assertNotIn(b"innerHTML", js, name)
            self.assertNotIn(b"insertAdjacentHTML", js, name)
            self.assertNotIn(b"outerHTML", js, name)
        status, _, js = helpers.request(self.d.port, "GET", "/ui/modules/transcript.js")
        self.assertIn(b"afterSeq", js)
        self.assertIn(b"/stop", js)
        run = self.start(handoff_id=self.move()["id"])
        self.wait_status(run["id"], "succeeded")
        status, headers, raw = helpers.request(self.d.port, "GET", f"/api/runs/{run['id']}/events",
                                               token=self.d.ui_token)
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        texts = [e["text"] for e in json.loads(raw)["events"] if e["kind"] == "assistant"]
        self.assertEqual(texts, ["<img src=x onerror=alert(1)>"])  # returned as data, rendered with textContent
        runs = self.d.get(f"/api/projects/{self.pid}/tickets/login/runs", token="ui")[1]["runs"]
        self.assertEqual([r["id"] for r in runs], [run["id"]])


class AutoHeadlessTests(RunnerCase):
    daemon_env = {"KANBAN_PICKUP_SECONDS": "1"}
    scenario = {"session_id": "sess-auto"}

    def test_auto_headless_on_pickup_timeout(self):
        sys.path.insert(0, str(helpers.TESTS))
        from test_runs_api import Stream
        stream = Stream(self.d.port, self.d.ui_token, self.pid)
        try:
            stream.next("hello")
            h = self.move()
            ev = stream.next("handoff.pickup_timeout", timeout=10)
            self.assertIsNotNone(ev, stream.seen)
            self.assertEqual((ev["data"]["id"], ev["data"]["action"], ev["data"]["runner"]), (h["id"], "headless", True))
        finally:
            stream.close()
        row = wait_for(lambda: kdb.live_run(self.db(), self.pid, "login") or self.db().execute(
            "SELECT * FROM runs WHERE ticket='login'").fetchone())
        run_id = row["id"]
        self.wait_status(run_id, "succeeded")
        run = self.run_row(run_id)
        self.assertEqual((run["kind"], run["handoff_id"]), ("headless", h["id"]))
        self.assertEqual(kdb.get_handoff(self.db(), h["id"])["status"], "done")
        self.assertEqual(kdb.get_ticket_session(self.db(), self.pid, "login"), "sess-auto")


if __name__ == "__main__":
    unittest.main(verbosity=2)
