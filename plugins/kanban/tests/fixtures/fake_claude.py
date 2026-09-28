#!/usr/bin/env python3
"""A stand-in for the `claude` CLI in runner tests (never the real binary; see KANBAN_CLAUDE_BIN).

The runner passes an allow-listed environment only, so the scenario comes from a file in the working directory (the
project root): `.fake-claude.json`, e.g. {"session_id": "sess-1", "text": "…", "wait": true, "grandchild": true,
"denials": [...], "resume_fail": true, "exit": 3, "finish": "failed", "stderr": "…",
"ignore_term": true}. Every start dumps
{argv, env, cwd, pid, pgid} to `.fake-claude/<KANBAN_RUN_ID>-<attempt>.json`. With "wait" it blocks until
`.fake-claude/release` or `.fake-claude/release-<run id>` exists, and it exits by itself when its parent (the daemon)
goes away, so no test leaves a process behind. Standard library only.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

CWD = Path.cwd()
OUT = CWD / ".fake-claude"
RUN_ID = os.environ.get("KANBAN_RUN_ID", "no-run")
PARENT = os.getppid()


def emit(obj) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def orphaned() -> bool:
    return os.getppid() != PARENT


def load_scenario() -> dict:
    try:
        return json.loads((CWD / ".fake-claude.json").read_text())
    except (OSError, ValueError):
        return {}


def dump() -> None:
    OUT.mkdir(exist_ok=True)
    attempt = len(list(OUT.glob(f"{RUN_ID}-*.json")))
    data = {"argv": sys.argv, "env": dict(os.environ), "cwd": os.getcwd(), "pid": os.getpid(),
            "pgid": os.getpgid(0), "sid": os.getsid(0)}
    tmp = OUT / f".{RUN_ID}-{attempt}.tmp"
    tmp.write_text(json.dumps(data))
    tmp.rename(OUT / f"{RUN_ID}-{attempt}.json")


def finish(outcome: str) -> None:
    """Call kanban_finish the way the MCP server of a headless run would (session with KANBAN_RUN_ID)."""
    import http.client
    home = Path(os.environ["KANBAN_HOME"])
    port = json.loads((home / "daemon.json").read_text())["port"]
    token = (home / "client.token").read_text().strip()

    def call(path, body, session=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": f"127.0.0.1:{port}", "Authorization": f"Bearer {token}",
                   "Content-Type": "application/json"}
        if session:
            headers["X-Kanban-Session"] = session
        conn.request("POST", path, body=json.dumps(body), headers=headers)
        out = json.loads(conn.getresponse().read() or b"null")
        conn.close()
        return out
    sid = call("/api/sessions", {"project_root": str(CWD), "claude_pid": os.getpid(), "claude_start_time": "",
                                 "kind": "headless", "channel": False, "run_id": RUN_ID})["session_id"]
    call("/api/runs/finish", {"outcome": outcome, "summary": "finished by the fake"}, session=sid)


def main() -> int:
    scenario = load_scenario()
    dump()
    if scenario.get("ignore_term"):  # only SIGKILL ends it: exercises the runner's 5 s escalation
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        (OUT / f"{RUN_ID}-term-ignored").write_text("")
    if "--resume" in sys.argv and scenario.get("resume_fail"):
        sys.stderr.write(f"No conversation found with session ID: {sys.argv[sys.argv.index('--resume') + 1]}\n")
        return 1
    session = scenario.get("session_id", "sess-fake")
    emit({"type": "system", "subtype": "init", "session_id": session, "cwd": str(CWD), "model": "fake",
          "tools": ["Read", "Edit"]})
    emit({"type": "assistant", "session_id": session,
          "message": {"role": "assistant", "content": [{"type": "text", "text": scenario.get("text", "Working on it")},
                                                       {"type": "tool_use", "id": "tu1", "name": "Read",
                                                        "input": {"file_path": "README.md"}}]}})
    emit({"type": "user", "session_id": session,
          "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1",
                                                   "content": "file contents"}]}})
    if scenario.get("stderr"):
        sys.stderr.write(scenario["stderr"] + "\n")
        sys.stderr.flush()
    if scenario.get("grandchild"):
        child = subprocess.Popen([sys.executable, "-c",
                                  "import os, time\nparent = os.getppid()\nend = time.time() + 60\n"
                                  "while time.time() < end and os.getppid() == parent:\n    time.sleep(0.1)\n"])
        OUT.mkdir(exist_ok=True)
        tmp = OUT / f".{RUN_ID}-grandchild.tmp"
        tmp.write_text(str(child.pid))
        tmp.rename(OUT / f"{RUN_ID}-grandchild.pid")  # atomic: readers never see an empty file
    if scenario.get("wait"):
        end = time.time() + 60
        while time.time() < end and not orphaned():
            if (OUT / "release").exists() or (OUT / f"release-{RUN_ID}").exists():
                break
            time.sleep(0.05)
    if scenario.get("finish"):
        finish(scenario["finish"])
    emit({"type": "result", "subtype": "success", "is_error": False, "session_id": session, "result": "done",
          "num_turns": 2, "permission_denials": scenario.get("denials", [])})
    return int(scenario.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
