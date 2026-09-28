"""Test isolation for the kanban plugin (stdlib only).

Importing this module points KANBAN_HOME at a throwaway folder and sets KANBAN_NO_DAEMON=1 for this process and every
subprocess it starts, so no test can reach (or create) the user's real KANBAN_HOME or a real daemon. Tests that need a
daemon start a throwaway foreground one with TestDaemon (own temp home, free port) and stop it in tearDown.
"""
from __future__ import annotations

import atexit
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PLUGIN = TESTS.parent
SCRIPTS = PLUGIN / "scripts"
FIXTURES = TESTS / "fixtures"
DAEMON_PY = SCRIPTS / "daemon.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

_SESSION_HOME = tempfile.mkdtemp(prefix="kanban-test-home-")
atexit.register(shutil.rmtree, _SESSION_HOME, True)
os.environ["KANBAN_HOME"] = _SESSION_HOME
os.environ["KANBAN_NO_DAEMON"] = "1"
os.environ.setdefault("KANBAN_POLL_SECONDS", "0.2")
os.environ["KANBAN_DAEMON_PORT"] = "0"  # daemons started by tests never take the default port
os.environ.pop("KANBAN_RUN_ID", None)
os.environ.pop("CLAUDE_PROJECT_DIR", None)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def temp_home() -> Path:
    """A fresh KANBAN_HOME for one test (the caller removes it)."""
    return Path(tempfile.mkdtemp(prefix="kanban-home-"))


def base_env(home: Path, **extra) -> dict:
    env = {**os.environ, "KANBAN_HOME": str(home), "KANBAN_NO_DAEMON": "1", **{k: str(v) for k, v in extra.items()}}
    for key in [k for k, v in extra.items() if v is None]:
        env.pop(key, None)
    return env


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def kill_home_daemon(home: Path, timeout: float = 5.0) -> None:
    """Stop whatever daemon a test started in `home` (SIGTERM, then SIGKILL)."""
    info = read_json(Path(home) / "daemon.json")
    pid = info and info.get("pid")
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    end = time.time() + timeout
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def request(port: int, method: str, path: str, body=None, token: str | None = None, headers: dict | None = None,
            timeout: float = 5.0):
    """(status, headers dict with lower-case names, body bytes)."""
    hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


class TestDaemon:
    """A throwaway `daemon.py --foreground` in its own KANBAN_HOME on a free port."""

    def __init__(self, home: Path | None = None, **env):
        self.own_home = home is None
        self.home = Path(home or temp_home())
        self.env = base_env(self.home, **env)
        self.proc = None
        self.log = self.home / "test-daemon.log"

    def start(self, timeout: float = 10.0) -> "TestDaemon":
        self.port = free_port()
        self._log = open(self.log, "ab")
        self.proc = subprocess.Popen([sys.executable, str(DAEMON_PY), "--foreground", "--port", str(self.port)],
                                     stdin=subprocess.DEVNULL, stdout=self._log, stderr=self._log, env=self.env)
        end = time.time() + timeout
        while time.time() < end:
            info = read_json(self.home / "daemon.json")
            if info and info.get("pid") == self.proc.pid:
                try:
                    if self.get("/api/health")[0] == 200:
                        break
                except OSError:
                    pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"test daemon exited: {self.log.read_text()}")
            time.sleep(0.05)
        else:
            self.stop()
            raise RuntimeError("test daemon did not become healthy")
        return self

    @property
    def client_token(self) -> str:
        return (self.home / "client.token").read_text().strip()

    @property
    def ui_token(self) -> str:
        return (self.home / "ui.token").read_text().strip()

    def token(self, which):
        return {"client": self.client_token, "ui": self.ui_token, None: None}.get(which, which)

    def call(self, method, path, body=None, token="client", headers=None):
        status, hdrs, raw = request(self.port, method, path, body, self.token(token), headers)
        try:
            return status, json.loads(raw or b"null")
        except ValueError:
            return status, raw

    def get(self, path, token="client"):
        return self.call("GET", path, token=token)

    def register(self, root: Path, kind="interactive", channel=False) -> dict:
        status, body = self.call("POST", "/api/sessions", {"project_root": str(root), "claude_pid": os.getpid(),
                                                           "claude_start_time": "", "kind": kind,
                                                           "channel": channel})
        assert status == 200, body
        return body

    def log_text(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.proc:
            self._log.close()
        if self.own_home:
            shutil.rmtree(self.home, ignore_errors=True)


def make_project(base: Path, name: str = "proj") -> Path:
    root = Path(base) / name
    (root / ".git").mkdir(parents=True)
    return root.resolve()
