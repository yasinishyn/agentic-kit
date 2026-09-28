"""Headless runner of the kanban daemon (PRD-04, ADR-004, architecture §5 steps 6–8, §8).

When no session picks a hand-off up (or the developer presses "Run headless"), the daemon runs

    claude -p <kanban_rules.headless_prompt> --output-format stream-json --verbose --permission-mode acceptEdits
           [--resume <the ticket's Claude session id>]

in the project root, in its own process group, with an allow-listed environment (HOME USER LOGNAME LANG LC_* TMPDIR
SHELL, the resolved PATH, KANBAN_HOME) plus CLAUDE_PROJECT_DIR, KANBAN_RUN_ID and KANBAN_HANDOFF_ID; CLAUDECODE and
every other CLAUDE_* of the daemon's environment stay out. Never --dangerously-skip-permissions.

`claude` is resolved from KANBAN_CLAUDE_BIN (tests), else the login shell's `command -v claude`, else
~/.local/bin/claude, /opt/homebrew/bin/claude, /usr/local/bin/claude; missing → the run fails with a clear reason and
the hand-off stays claimable. Under KANBAN_NO_DAEMON=1 (test isolation, tests/helpers.py) only KANBAN_CLAUDE_BIN is
honoured, so no test can ever start the real CLI.

Each stream-json line becomes a run event (run_events: seq = row id, kind, text; published live as `run.event`);
system/init.session_id is kept per ticket (ticket_sessions) for --resume; a resumed start that fails (non-zero exit
without an init, or quickly with a resume error) is retried once without --resume; result.permission_denials become
`permission_denial` events (the card shows a badge). Exit 0 → succeeded, else failed, unless the run already ended
(kanban_finish, Stop). Stop: the run is cancelled, then SIGTERM to the process group, 5 s, SIGKILL. At most
KANBAN_MAX_RUNS (4) processes; extra runs stay queued and start when a slot frees. At start-up, live headless runs of
a previous daemon are failed (their process group is terminated when it is still the same process) and run events
older than 30 days are deleted. Standard library only.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

import kanban_db as kdb
import kanban_rules as rules

ENV_ALLOW = ("HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "SHELL")
KNOWN_PATHS = ("~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude")
STOP_GRACE = 5.0
RETENTION_SECONDS = 30 * 86400
MAX_TEXT = 20000       # one stored event
LIVE_TEXT = 4000       # one published run.event (the transcript fetches the full text)
RESUME_QUICK = 20.0    # a resumed start that dies this fast with a resume error is retried
RESUME_ERROR = re.compile(r"resum|conversation|session", re.I)
LOOP_SECONDS = 0.5


class Conflict(ValueError):
    """The request cannot be carried out in the current state (HTTP 409)."""


# ---------------------------------------------------------------- pure helpers
def build_argv(claude: str, prompt: str, resume: str | None = None) -> list:
    argv = [claude, "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits"]
    return argv + ["--resume", resume] if resume else argv


def child_env(base, root: str, run_id: str, handoff_id: str, path: str, home: str) -> dict:
    """The allow-listed environment of a headless run (architecture §5 step 7)."""
    env = {k: v for k, v in base.items() if k in ENV_ALLOW or k.startswith("LC_")}
    env.update(PATH=path, KANBAN_HOME=str(home), CLAUDE_PROJECT_DIR=str(root), KANBAN_RUN_ID=run_id,
               KANBAN_HANDOFF_ID=handoff_id)
    return env


def _executable(path) -> bool:
    return bool(path) and os.path.isabs(str(path)) and os.path.isfile(path) and os.access(path, os.X_OK)


def _login_shell(env, command: str) -> str:
    shell = env.get("SHELL") or "/bin/sh"
    try:
        out = subprocess.run([shell, "-lc", command], capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL)
        lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
        return lines[-1] if out.returncode == 0 and lines else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def resolve_claude(env=None) -> str | None:
    env = os.environ if env is None else env
    override = env.get("KANBAN_CLAUDE_BIN")
    if override:
        return override if _executable(override) else None
    if env.get("KANBAN_NO_DAEMON") == "1":
        return None
    found = _login_shell(env, "command -v claude")
    if _executable(found):
        return found
    for candidate in KNOWN_PATHS:
        path = os.path.expanduser(candidate)
        if _executable(path):
            return path
    return None


def resolve_path(claude: str, env=None) -> str:
    """The login shell's PATH (the daemon may have been started with a minimal one), with claude's folder first."""
    env = os.environ if env is None else env
    path = "" if env.get("KANBAN_CLAUDE_BIN") else _login_shell(env, 'printf "%s\\n" "$PATH"')
    path = path or env.get("PATH") or "/usr/bin:/bin:/usr/sbin:/sbin"
    folder = os.path.dirname(claude)
    parts = [p for p in path.split(os.pathsep) if p]
    return os.pathsep.join([folder] + [p for p in parts if p != folder])


def _clip(text, limit: int = MAX_TEXT) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + " …"


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") if isinstance(c, dict) and c.get("type") == "text" else _clip(c, 2000)
                         for c in content)
    return _clip(content)


def parse_line(line: str) -> dict:
    """One stdout line → {"events": [(kind, text)], "session_id": str|None, "denials": int}."""
    out = {"events": [], "session_id": None, "denials": 0}
    line = line.strip()
    if not line:
        return out
    try:
        obj = json.loads(line)
    except ValueError:
        obj = None
    if not isinstance(obj, dict):
        out["events"].append(("output", _clip(line)))
        return out
    kind = obj.get("type")
    events = out["events"]
    if kind == "system":
        if obj.get("subtype") == "init" and obj.get("session_id"):
            out["session_id"] = str(obj["session_id"])
            events.append(("system", f"session {obj['session_id']} · model {obj.get('model', '?')}"))
        else:
            events.append(("system", _clip({k: v for k, v in obj.items() if k != "type"})))
    elif kind in ("assistant", "user"):
        content = (obj.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": _content_text(content)}]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                events.append((kind, _clip(block.get("text", ""))))
            elif btype == "thinking":
                events.append(("thinking", _clip(block.get("thinking", ""))))
            elif btype == "tool_use":
                events.append(("tool_use", _clip(f"{block.get('name', '?')} {_clip(block.get('input', {}), 4000)}")))
            elif btype == "tool_result":
                prefix = "error: " if block.get("is_error") else ""
                events.append(("tool_result", _clip(prefix + _content_text(block.get("content", "")))))
            else:
                events.append((btype or kind, _clip(block)))
    elif kind == "result":
        text = f"{obj.get('subtype', 'result')}: {_content_text(obj.get('result', ''))}"
        if obj.get("total_cost_usd") is not None:
            text += f" (cost ${obj['total_cost_usd']}, {obj.get('num_turns', '?')} turns)"
        events.append(("result", _clip(text)))
        for denial in obj.get("permission_denials") or []:
            if isinstance(denial, dict):
                desc = f"{denial.get('tool_name', '?')} {_clip(denial.get('tool_input', {}), 4000)}"
            else:
                desc = _clip(denial, 4000)
            events.append(("permission_denial", _clip(desc)))
            out["denials"] += 1
    else:
        events.append((str(kind or "event"), _clip({k: v for k, v in obj.items() if k != "type"})))
    return out


def event_view(row) -> dict:
    """A run_events row as the transcript API returns it: {seq, at, kind, text}."""
    row = dict(row)
    try:
        data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    etype = row["type"]
    if etype == "status":
        text = f"{data['from']} → {data['to']}" if data.get("from") else str(data.get("to"))
        if data.get("reason"):
            text += f" · {data['reason']}"
    elif etype == "subtask":
        text = str(data.get("path", ""))
    else:
        text = str(data.get("text", "")) if "text" in data else json.dumps(data)
    return {"seq": row["id"], "at": row["at"], "kind": data.get("kind", etype) if etype not in ("status", "subtask")
            else etype, "text": text}


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def kill_group(pgid: int, grace: float = STOP_GRACE, proc: subprocess.Popen | None = None) -> None:
    """SIGTERM to the process group, up to `grace` seconds, then SIGKILL."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    end = time.time() + grace
    while time.time() < end:
        if proc is not None:
            proc.poll()  # reap our child so a zombie does not keep the group "alive"
        if not _group_alive(pgid):
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


# ---------------------------------------------------------------- the runner
class Runner:
    """Owns the headless processes of one daemon. `transition(conn, run_id, status, reason)` and
    `changed(conn, run_id)` come from routes_runs (they publish run.changed and requeued hand-offs)."""

    def __init__(self, ctx, transition, changed, max_runs: int | None = None):
        self.ctx, self.transition, self.changed = ctx, transition, changed
        if max_runs is None:
            try:
                max_runs = int(os.environ.get("KANBAN_MAX_RUNS") or 4)
            except ValueError:
                max_runs = 4
        self.max_runs = max(1, max_runs)
        self.procs: dict = {}       # run id → Popen (live processes; the concurrency count)
        self.stopping: set = set()  # run ids being stopped (their exit does not decide the state)
        self.lock = threading.RLock()
        self.pump_lock = threading.Lock()
        self._claude = None
        self._path = None
        atexit.register(self.shutdown)

    # ---- resolution (cached once found)
    def claude(self) -> str | None:
        if self._claude is None:
            self._claude = resolve_claude()
        return self._claude

    def path(self, claude: str) -> str:
        if self._path is None:
            self._path = resolve_path(claude)
        return self._path

    # ---- events
    def event(self, conn, run: dict, kind: str, text: str, etype: str = "transcript") -> int:
        cur = conn.execute("INSERT INTO run_events (run_id, at, type, data) VALUES (?, ?, ?, ?)",
                           (run["id"], time.time(), etype, json.dumps({"kind": kind, "text": text})))
        seq = cur.lastrowid
        self.ctx.publish(run["project_id"], "run.event", {"run_id": run["id"], "ticket": run["ticket"], "seq": seq,
                                                          "kind": kind, "text": _clip(text, LIVE_TEXT)})
        return seq

    def _end(self, conn, run_id: str, status: str, reason: str | None) -> None:
        try:
            self.transition(conn, run_id, status, reason)
        except (KeyError, ValueError):
            pass  # already terminal (kanban_finish or Stop won): terminal states are sticky

    # ---- start
    def start(self, project_id: str, ticket: str, handoff_id: str | None = None) -> dict:
        """Queue a headless run for the ticket's pending hand-off and start it when a slot is free."""
        conn = self.ctx.db()
        if kdb.live_run(conn, project_id, ticket):
            raise Conflict("the ticket already has a live run")
        pending = kdb.pending_handoffs(conn, project_id, ticket)
        if handoff_id:
            match = [h for h in pending if h["id"] == handoff_id]
            if not match:
                raise Conflict("that hand-off is not pending for this ticket")
            handoff = match[0]
        elif pending:
            handoff = pending[-1]
        else:
            raise Conflict("no pending hand-off for this ticket")
        try:
            run = kdb.create_run(conn, project_id, ticket, handoff["stage"], "headless", status="queued",
                                 handoff_id=handoff["id"])
        except kdb.LiveRunExists as exc:
            raise Conflict("the ticket already has a live run") from exc
        self.changed(conn, run["id"])
        self.pump()
        return kdb.get_run(conn, run["id"])

    def start_for_handoff(self, ctx, handoff: dict) -> str:
        """routes_runs.start_headless: the pickup timer's automatic start (same path as the button)."""
        run = self.start(handoff["project_id"], handoff["ticket"], handoff["id"])
        if run["status"] == "failed":
            raise RuntimeError(run["reason"])
        return run["id"]

    def pump(self) -> None:
        """Start queued headless runs, oldest first, while fewer than max_runs processes are alive."""
        with self.pump_lock:
            conn = self.ctx.db()
            for run in kdb.live_runs(conn):
                if run["kind"] != "headless" or run["status"] != "queued":
                    continue
                with self.lock:
                    if len(self.procs) >= self.max_runs:
                        return
                try:
                    self._launch(conn, run)
                except Exception as exc:  # never leave a queued run behind silently
                    self.ctx.log(f"headless run {run['id']} could not start: {exc!r}")
                    self._end(conn, run["id"], "failed", f"could not start: {exc}")

    def _launch(self, conn, run: dict) -> None:
        project = kdb.get_project(conn, run["project_id"])
        handoff = kdb.get_handoff(conn, run["handoff_id"]) if run["handoff_id"] else None
        if project is None or handoff is None:
            self._end(conn, run["id"], "failed", "its project or hand-off is gone")
            return
        claude = self.claude()
        if claude is None:
            where = "KANBAN_CLAUDE_BIN" if os.environ.get("KANBAN_CLAUDE_BIN") else \
                "the login shell PATH, " + ", ".join(KNOWN_PATHS)
            self._end(conn, run["id"], "failed", f"claude CLI not found (looked in {where}); install Claude Code or "
                                                 "pick the hand-off up in a session")
            return
        result, _ = kdb.claim(conn, handoff["id"], run_id=run["id"], kind="headless")
        if result != "ok":
            self._end(conn, run["id"], "cancelled", f"hand-off {result}")
            return
        self.changed(conn, run["id"])
        resume = kdb.get_ticket_session(conn, run["project_id"], run["ticket"])
        prompt = rules.headless_prompt({**handoff, "handoff_id": handoff["id"]})
        spec = {"claude": claude, "prompt": prompt, "root": project["root"], "handoff_id": handoff["id"]}
        proc = self._spawn(conn, run, spec, resume)
        threading.Thread(target=self._watch, args=(run, spec, proc, resume), name=f"run-{run['id']}",
                         daemon=True).start()

    def _spawn(self, conn, run: dict, spec: dict, resume: str | None) -> subprocess.Popen:
        env = child_env(os.environ, spec["root"], run["id"], spec["handoff_id"], self.path(spec["claude"]),
                        str(kdb.kanban_home()))
        with self.lock:
            proc = subprocess.Popen(build_argv(spec["claude"], spec["prompt"], resume), cwd=spec["root"], env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            self.procs[run["id"]] = proc
        conn.execute("UPDATE runs SET pid=?, claude_pid=?, claude_start_time=? WHERE id=?",
                     (proc.pid, proc.pid, kdb.process_start_time(proc.pid), run["id"]))
        return proc

    # ---- one process (and its single retry) from start to exit
    def _watch(self, run: dict, spec: dict, proc: subprocess.Popen, resume: str | None) -> None:
        conn = self.ctx.db()
        try:
            while True:
                started, saw_init, tail = time.time(), False, []
                for raw in iter(proc.stdout.readline, b""):
                    parsed = parse_line(raw.decode("utf-8", errors="replace"))
                    if parsed["session_id"]:
                        saw_init = True
                        kdb.set_ticket_session(conn, run["project_id"], run["ticket"], parsed["session_id"])
                        conn.execute("UPDATE runs SET claude_session_id=? WHERE id=?", (parsed["session_id"],
                                                                                          run["id"]))
                    for kind, text in parsed["events"]:
                        self.event(conn, run, kind, text,
                                   "permission_denial" if kind == "permission_denial" else "transcript")
                        if kind == "output":
                            tail = (tail + [text])[-20:]
                    conn.execute("UPDATE runs SET heartbeat_at=? WHERE id=? AND status IN ('running', 'waiting')",
                                 (time.time(), run["id"]))
                code = proc.wait()
                proc.stdout.close()
                resume_failed = resume and code != 0 and run["id"] not in self.stopping and (
                    not saw_init or (time.time() - started < RESUME_QUICK and RESUME_ERROR.search("\n".join(tail))))
                current = kdb.get_run(conn, run["id"])
                if resume_failed and current and current["status"] in kdb.RUN_LIVE:
                    self.event(conn, run, "runner", f"resuming session {resume} failed (exit code {code}); "
                                                    "retrying once without --resume", "runner")
                    resume = None
                    proc = self._spawn(conn, run, spec, None)
                    continue
                break
            self.event(conn, run, "exit", f"exited with code {code}" if code >= 0 else
                       f"killed by signal {-code}", "exit")
            with self.lock:
                self.procs.pop(run["id"], None)
                stopped = run["id"] in self.stopping
                self.stopping.discard(run["id"])
            if not stopped:
                current = kdb.get_run(conn, run["id"])
                if code == 0 and current and current["status"] == "waiting":
                    # kanban_finish(needs_input), then the process ended: closed as succeeded, but the question
                    # stays visible on the card until the ticket is moved or a new run starts (kit owner's ruling)
                    self.event(conn, run, "needs_input", current["reason"] or "", "needs_input")
                if code == 0:
                    self._end(conn, run["id"], "succeeded", None)
                else:
                    last = f": {tail[-1][:200]}" if tail else ""
                    self._end(conn, run["id"], "failed", f"claude exited with code {code}{last}")
        except Exception as exc:
            self.ctx.log(f"headless run {run['id']}: {exc!r}")
            with self.lock:
                self.procs.pop(run["id"], None)
            self._end(conn, run["id"], "failed", f"runner error: {exc}")
        finally:
            self.pump()

    # ---- stop
    def stop(self, run_id: str) -> dict:
        conn = self.ctx.db()
        run = kdb.get_run(conn, run_id)
        if run is None:
            raise KeyError(f"unknown run {run_id}")
        if run["kind"] != "headless":
            raise Conflict("only headless runs can be stopped from the board; end an interactive run in its session")
        if run["status"] not in kdb.RUN_LIVE:
            raise Conflict(f"the run already ended ({run['status']})")
        with self.lock:
            proc = self.procs.get(run_id)
            if proc is not None:
                self.stopping.add(run_id)
        self._end(conn, run_id, "cancelled", "stopped from the board")
        if proc is not None:
            threading.Thread(target=kill_group, args=(proc.pid, STOP_GRACE, proc), name=f"stop-{run_id}",
                             daemon=True).start()
        return kdb.get_run(conn, run_id)

    # ---- daemon lifecycle
    def on_startup(self, ctx) -> None:
        conn = ctx.db()
        for run in kdb.live_runs(conn):
            if run["kind"] != "headless":
                continue
            pid, start = run["pid"], " ".join(str(run["claude_start_time"] or "").split())
            if pid and start and kdb.process_start_time(pid) == start:  # still the same process: end its group
                threading.Thread(target=kill_group, args=(int(pid),), daemon=True).start()
            self._end(conn, run["id"], "failed", "the board service restarted while the run was live")
        conn.execute("DELETE FROM run_events WHERE at < ?", (time.time() - RETENTION_SECONDS,))
        threading.Thread(target=self._loop, name="runner-loop", daemon=True).start()

    def _loop(self) -> None:
        while True:
            time.sleep(LOOP_SECONDS)
            try:
                self.pump()
                conn = self.ctx.db()
                with self.lock:
                    live = [(rid, p) for rid, p in self.procs.items() if rid not in self.stopping]
                for rid, proc in live:  # cancelled elsewhere (e.g. daemon.py --stop --cancel-runs): end the process
                    run = kdb.get_run(conn, rid)
                    if run and run["status"] == "cancelled":
                        with self.lock:
                            self.stopping.add(rid)
                        threading.Thread(target=kill_group, args=(proc.pid, STOP_GRACE, proc), daemon=True).start()
            except Exception as exc:
                self.ctx.log(f"runner loop: {exc!r}")

    def shutdown(self) -> None:
        """At daemon exit: terminate every headless process group (the next start marks the runs failed)."""
        with self.lock:
            procs = list(self.procs.values())
        for proc in procs:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        end = time.time() + 2.0
        while procs and time.time() < end and any(_group_alive(p.pid) for p in procs):
            for p in procs:
                p.poll()
            time.sleep(0.05)
        for proc in procs:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
