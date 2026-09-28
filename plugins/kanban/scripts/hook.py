#!/usr/bin/env python3
"""Claude Code hook for the kanban live indicator (architecture §6, ADR-005); hooks/hooks.json runs it on
PostToolUse and Stop.

Reads the hook JSON from stdin and tells the board daemon about activity: POST /api/hooks/activity with the event and
this process's ancestor pids with their start times, so the daemon can match the run of the `claude` process the MCP
server registered (pid + start time). PostToolUse is throttled to one POST per 10 s per Claude session (a small state
file under KANBAN_HOME/hooks); Stop is always sent and resets the session's throttle. The request times out after
1 s. It never blocks or fails a tool call: it prints nothing and exits 0 on any error, and exits at once when
KANBAN_HOME/daemon.json is absent (no daemon has ever run). Standard library only; imports kept light.
"""
import os
import sys

THROTTLE_SECONDS = 10.0
TIMEOUT = 1.0
EVENTS = ("PostToolUse", "Stop")


def kanban_home() -> str:
    home = os.environ.get("KANBAN_HOME")
    if home:
        return os.path.expanduser(home)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Kanban")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "kanban")


def ancestors() -> list:
    """[[pid, start time], …] from this process's parent up to 4 levels (stopping at pid 1 or a missing pid), one
    `ps -p` per level: the hook is claude's child or grandchild (via a shell), never deeper."""
    import subprocess
    env = {**os.environ, "LC_ALL": "C"}
    chain, pid = [], os.getppid()
    while pid > 1 and len(chain) < 4:
        out = subprocess.run(["ps", "-o", "ppid=,lstart=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=TIMEOUT, env=env).stdout.split()
        if len(out) < 2 or not out[0].isdigit():
            break
        chain.append([pid, " ".join(out[1:])])
        pid = int(out[0])
    return chain


def throttled(home: str, session: str, event: str) -> bool:
    """True when this PostToolUse must be skipped; records the time otherwise. Stop clears the session's record."""
    import fcntl
    import hashlib
    import time
    folder = os.path.join(home, "hooks")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, hashlib.sha256(session.encode()).hexdigest()[:24])
    if event == "Stop":
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return False
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = os.read(fd, 64).decode(errors="ignore").strip()
        now = time.time()
        try:
            last = float(raw)
        except ValueError:
            last = 0.0
        if 0 <= now - last < THROTTLE_SECONDS:
            return True
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"{now:.3f}".encode())
        return False
    finally:
        os.close(fd)


def main() -> None:
    home = kanban_home()
    if not os.path.exists(os.path.join(home, "daemon.json")):
        return
    import json
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    event = data.get("hook_event_name")
    if event not in EVENTS:
        return
    session = str(data.get("session_id") or os.getppid())
    if throttled(home, session, event):
        return
    with open(os.path.join(home, "daemon.json")) as handle:
        port = int(json.load(handle)["port"])
    with open(os.path.join(home, "client.token")) as handle:
        token = handle.read().strip()
    body = json.dumps({"event": event, "pids": ancestors()}).encode()
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
    try:
        conn.request("POST", "/api/hooks/activity", body=body, headers={
            "Host": f"127.0.0.1:{port}", "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        conn.getresponse().read()
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # a hook never fails or blocks the tool call
        pass
    sys.exit(0)
