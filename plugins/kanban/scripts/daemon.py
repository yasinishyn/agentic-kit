#!/usr/bin/env python3
"""Kanban board daemon (kanband): one per user, serving every registered project's board (architecture §3–§4, §8).

    daemon.py --ensure              start it detached if needed (idempotent), print the board URL
    daemon.py --open [--project D]  ensure, register the project D (default: the current project), open the browser
    daemon.py --stop [--cancel-runs]  stop it; refused while runs are live unless --cancel-runs
    daemon.py --foreground [--port N]  run in this process (what --ensure spawns; used by tests)

State: KANBAN_HOME (0700; default ~/Library/Application Support/Kanban on macOS, $XDG_DATA_HOME/kanban elsewhere)
holds daemon.lock (flock, taken before bind and held for life), daemon.json (port, pid, api, version; written after
bind; no secrets), client.token and ui.token (0600), kanban.db (kanban_db) and daemon.log.

HTTP on 127.0.0.1 only. `/` and `/ui/*` are static and unauthenticated (no data); every `/api/*` needs
`Authorization: Bearer <token>`: the client token (MCP server, hooks) grants scopes read + client, the UI token grants
read + ui. Host must be 127.0.0.1/localhost, a foreign Origin is refused, no CORS headers are ever sent, and every
response carries the CSP of architecture §8. Rejected requests read and discard their body before replying.

Events: `GET /api/events[?project=<id>]` answers 200 with `Content-Type: application/x-ndjson` and keeps the response
open: one JSON object per line, `{"id", "event", "project", "data", "at"}`; the first line is `hello`, a `ping` line
is sent after 15 s of silence, the stream ends when the daemon stops. At most 32 subscribers (503 beyond). A
subscription sent with `X-Kanban-Session: <session id>` makes that session live (ctx.live_sessions) while it is open.

Plug-ins: every `scripts/routes_*.py` (plus KANBAN_ROUTES_DIRS, os.pathsep-separated, for tests) is imported at start in
name order and its `register(ctx)` called; a module that fails is logged and skipped (see PluginContext).
Standard library only.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hmac
import http.client
import http.server
import importlib.util
import json
import os
import queue
import re
import secrets
import select
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import kanban_db as kdb  # noqa: E402
import kanban_md as km  # noqa: E402
import kanban_rules as rules  # noqa: E402
import mdview  # noqa: E402

API = int(os.environ.get("KANBAN_DAEMON_API") or 1)  # env overrides exist for tests (version negotiation)
VERSION = os.environ.get("KANBAN_DAEMON_VERSION") or "0.3.0"
DEFAULT_PORT = 47821
UI_DIR = Path(os.environ.get("KANBAN_UI_DIR") or SCRIPTS.parent / "ui").resolve()
EDITOR_URL = os.environ.get("KANBAN_EDITOR_URL", "vscode://file/{path}")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self' ipc: http://ipc.localhost; frame-ancestors 'none'")
SCOPES = ("read", "client", "ui")
TOKEN_SCOPES = {"client": ("read", "client"), "ui": ("read", "ui")}
MAX_SUBSCRIBERS = 32
MAX_BODY = 4 * 1024 * 1024
PING_SECONDS = 15.0
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# human-only endpoints: whoever registers them must use the ui scope, and a non-UI token is refused even before a
# later PRD registers the route (architecture §8 scope matrix)
UI_ONLY = (("POST", re.compile(r"^/api/projects/[^/]+/tickets/[^/]+/(move|approve)$")),
           ("PUT", re.compile(r"^/api/projects/[^/]+/files(/.*)?$")),
           ("POST", re.compile(r"^/api/(.+/)?runs(/[^/]+)?/(start|stop)$")))
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
                 ".png": "image/png", ".woff2": "font/woff2", ".map": "application/json"}
STREAMED = object()  # a handler that wrote its own response returns this


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} kanband[{os.getpid()}]: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- home, tokens, daemon.json
def _write_private(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def ensure_tokens(home: Path) -> dict:
    """{'client': …, 'ui': …}; created once (0600) and kept across restarts."""
    tokens = {}
    for name in ("client", "ui"):
        path = home / f"{name}.token"
        if not path.exists():
            # publish atomically: a concurrent reader must never see an empty token file
            tmp = home / f".{name}.token.{os.getpid()}.{secrets.token_hex(4)}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_urlsafe(32) + "\n")
            try:
                os.link(tmp, path)  # fails if another process published first; theirs wins
            except FileExistsError:
                pass
            finally:
                os.unlink(tmp)
        os.chmod(path, 0o600)
        value = path.read_text().strip()
        if not value:
            raise RuntimeError(f"{path} is empty; delete it to create a new token")
        tokens[name] = value
    return tokens


def read_info(home: Path) -> dict | None:
    try:
        info = json.loads((home / "daemon.json").read_text())
        return info if isinstance(info, dict) else None
    except (OSError, ValueError):
        return None


def board_url(info: dict) -> str:
    return f"http://127.0.0.1:{info['port']}/"


def ui_url(info: dict, home: Path) -> str:
    """The browser-mode URL: the UI token rides in the fragment (never sent to the server, kept in sessionStorage)."""
    return board_url(info) + "#t=" + ensure_tokens(home)["ui"]


process_start_time = kdb.process_start_time  # the one helper (server.py and tests use kd.process_start_time)


def headless_run_for(conn, project_id: str, claude_pid) -> dict | None:
    """The live headless run of this project whose claude process is `claude_pid` (pid and `ps` start time both
    match, so a reused pid never matches), or None."""
    try:
        pid = int(claude_pid)
    except (TypeError, ValueError):
        return None
    if pid <= 1:
        return None
    runs = [r for r in kdb.live_runs(conn, project_id) if r["kind"] == "headless" and r["claude_pid"] == pid]
    start = process_start_time(pid) if runs else ""
    if not start:
        return None
    return next((r for r in runs if " ".join(str(r["claude_start_time"] or "").split()) == start), None)


def api_request(port: int, method: str, path: str, body=None, token: str | None = None, session: str | None = None,
                timeout: float = 3.0) -> tuple:
    """(status, parsed JSON or None) for one request to the daemon."""
    headers = {"Host": f"127.0.0.1:{port}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session:
        headers["X-Kanban-Session"] = session
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
    finally:
        conn.close()
    try:
        return resp.status, json.loads(raw) if raw else None
    except ValueError:
        return resp.status, None


# ---------------------------------------------------------------- events
class Subscriber:
    def __init__(self, project: str | None, session_id: str | None):
        self.project, self.session_id = project, session_id
        self.queue = queue.Queue(maxsize=512)
        self.overflow = False


class EventBus:
    def __init__(self, limit: int = MAX_SUBSCRIBERS):
        self.limit, self.subs, self.seq = limit, [], 0
        self.lock = threading.Lock()

    def subscribe(self, project: str | None, session_id: str | None) -> Subscriber | None:
        with self.lock:
            if len(self.subs) >= self.limit:
                return None
            sub = Subscriber(project, session_id)
            self.subs.append(sub)
            return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self.lock:
            if sub in self.subs:
                self.subs.remove(sub)

    def publish(self, project_id: str | None, event: str, data=None) -> dict:
        with self.lock:
            self.seq += 1
            ev = {"id": self.seq, "event": event, "project": project_id, "data": data or {}, "at": time.time()}
            for sub in self.subs:  # an event without a project (project.registered) reaches every subscriber
                if project_id is None or sub.project in (None, project_id):
                    try:
                        sub.queue.put_nowait(ev)
                    except queue.Full:
                        sub.overflow = True  # a stalled reader is dropped; it reconnects and reloads
        return ev

    def session_ids(self) -> set:
        with self.lock:
            return {s.session_id for s in self.subs if s.session_id}


def _peer_closed(sock) -> bool:
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return False
        return sock.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


# ---------------------------------------------------------------- requests, routes, plug-in context
class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


class Route:
    def __init__(self, method: str, pattern: str, handler, scope: str, owner: str):
        self.method, self.pattern, self.handler, self.scope, self.owner = method, pattern, handler, scope, owner
        parts = re.split(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", pattern)
        regex = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>[^/]+)" for i, p in enumerate(parts))
        self.regex = re.compile(f"^{regex}$")


class Request:
    """What a route handler receives."""

    def __init__(self, daemon, handler, method, path, query, params, body, scope, session):
        self.daemon, self.handler, self.method, self.path = daemon, handler, method, path
        self.query, self.params, self.body, self.scope, self.session = query, params, body, scope, session
        self.headers = handler.headers
        self._json = None

    @property
    def json(self) -> dict:
        if self._json is None:
            try:
                value = json.loads(self.body or b"{}")
            except ValueError:
                raise ApiError(400, "body is not JSON")
            if not isinstance(value, dict):
                raise ApiError(400, "body must be a JSON object")
            self._json = value
        return self._json

    def project(self) -> dict:
        project = kdb.get_project(self.daemon.store.conn(), self.params.get("project", ""))
        if project is None:
            raise ApiError(404, "unknown project")
        return project

    def ticket(self, project: dict) -> str:
        ticket = self.params.get("ticket", "")
        if not TICKET_RE.match(ticket) or ".." in ticket:
            raise ApiError(400, "invalid ticket id")
        try:
            km.ticket_readme(Path(project["root"]), ticket)
        except KeyError:
            raise ApiError(404, f"no ticket {ticket}")
        return ticket


class PluginContext:
    """`ctx` handed to `register(ctx)` of every routes_*.py (architecture §3.1).

    route(method, pattern, handler, scope)  pattern like "/api/projects/{project}/runs"; scope read | client | ui;
                                            handler(req) returns a dict/list (200 JSON), (status, obj), or STREAMED
    db()                                    this thread's sqlite3 connection (kanban_db schema)
    project(id) -> Path                     root from the registry (KeyError if unknown); clients never send paths
    publish(project_id, event, data)        to every open /api/events subscription of that project
    live_sessions(project_id, channel=None) sessions whose events subscription is open (channel filter optional)
    record_approval(project_id, ticket, actor)  the single approval writer (markdown + approvals row + snapshot)
    on_startup(fn(ctx))                     after all plug-ins registered, before serving
    on_human_move(fn(project_id, ticket, src, dst, handoff))  after a UI move (handoff dict or None)
    settings                                kanban_db.Settings (get/set/items)
    ApiError, STREAMED, log                 helpers
    """

    ApiError = ApiError
    STREAMED = STREAMED

    def __init__(self, daemon):
        self._daemon = daemon
        self._owner = "core"
        self.settings = kdb.Settings(daemon.store)
        self.log = log

    def route(self, method: str, pattern: str, handler, scope: str) -> None:
        method = method.upper()
        if method not in METHODS:
            raise ValueError(f"method must be one of {', '.join(METHODS)}")
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
        if not pattern.startswith("/api/"):
            raise ValueError("plug-in routes live under /api/")
        route = Route(method, pattern, handler, scope, self._owner)
        probe = re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "x", pattern)
        if _ui_only(method, probe) and scope != "ui":
            raise ValueError(f"{method} {pattern} is a human-only endpoint: its scope must be ui")
        if any(r.method == method and r.pattern == pattern for r in self._daemon.routes + self._daemon._staged):
            raise ValueError(f"{method} {pattern} is already registered")
        self._daemon._staged.append(route)

    def db(self):
        return self._daemon.store.conn()

    def project(self, project_id: str) -> Path:
        project = kdb.get_project(self.db(), project_id)
        if project is None:
            raise KeyError(f"unknown project {project_id}")
        return Path(project["root"])

    def publish(self, project_id: str, event: str, data=None) -> dict:
        return self._daemon.bus.publish(project_id, event, data)

    def live_sessions(self, project_id: str, channel: bool | None = None) -> list:
        return self._daemon.live_sessions(project_id, channel)

    def record_approval(self, project_id: str, ticket: str, actor: str, session_id: str | None = None) -> dict:
        return self._daemon.record_approval(project_id, ticket, actor, session_id)

    def on_startup(self, fn) -> None:
        self._daemon._staged_hooks.append(("startup", fn))

    def on_human_move(self, fn) -> None:
        self._daemon._staged_hooks.append(("human_move", fn))


PROJECT_MARKERS = (".SDD", ".git", ".claude")


def project_root(body: dict) -> str:
    """The request's project_root: an existing absolute directory holding .SDD, .git or .claude (else 400)."""
    root = str(body.get("project_root") or "")
    path = Path(root)
    if not root or not path.is_absolute() or not path.is_dir():
        raise ApiError(400, "project_root must be an existing absolute directory")
    if not any((path / marker).exists() for marker in PROJECT_MARKERS):
        raise ApiError(400, f"project_root must contain one of {', '.join(PROJECT_MARKERS)}")
    return root


def _ui_only(method: str, path: str) -> bool:
    return any(m == method and rx.match(path) for m, rx in UI_ONLY)


# ---------------------------------------------------------------- board
def _approval_view(conn, project_id: str, ticket: dict, record: dict | None, state: str) -> dict:
    """Board approval info. `legacy`: in EXECUTION with no record (approved before v0.3, Q14) — a badge only."""
    info = {"state": "none", "by": "", "at": "", "recorded": False, "board_recorded": False,
            "legacy": record is None and ticket.get("status") in rules.EXECUTION}
    if record is not None:
        latest = kdb.latest_approval(conn, project_id, ticket["id"])
        recorded = bool(latest and latest["hash"] == record["hash"])
        info.update(state=state, by=record["by"], at=record["at"], recorded=recorded,
                    board_recorded=recorded and latest["actor"] == "human (board)")
    return info


def approval_info(conn, project_id: str, root: Path, ticket: dict) -> dict:
    """README approval state plus whether the DB recorded it (architecture §7 detection)."""
    record, state = None, "none"
    if ticket.get("path"):
        readme = root / ticket["path"]
        fields = km.split_frontmatter(readme.read_text())[0]
        record = rules.approval_record(fields)
        if record is not None:
            state = rules.approval_state(fields, km.spec_hash(readme.parent))
    return _approval_view(conn, project_id, ticket, record, state)


_TICKET_CACHE: dict = {}  # folder → (signature, ticket, readme approval record, approval state)
_TICKET_CACHE_LOCK = threading.Lock()


def folder_signature(folder: str) -> tuple:
    """(relative path, mtime_ns, size, inode) of every file under a ticket folder: any edit, atomic replace, add or
    delete changes it, so a cached parse is reused only while the ticket's markdown is untouched."""
    out = []
    for base, dirs, files in os.walk(folder):
        dirs.sort()
        for name in files:
            try:
                st = os.stat(os.path.join(base, name))
            except OSError:
                continue
            out.append((os.path.join(base, name), st.st_mtime_ns, st.st_size, st.st_ino))
    return tuple(sorted(out))


def _cached_ticket(root: Path, folder: str) -> tuple:
    """(ticket dict, approval record, approval state) parsed from markdown, cached per folder signature."""
    sig = folder_signature(folder)
    with _TICKET_CACHE_LOCK:
        hit = _TICKET_CACHE.get(folder)
    if hit and hit[0] == sig:
        return hit[1:]
    path = Path(folder)
    ticket = km.read_ticket(root, path)
    record, state = None, "none"
    if ticket.get("path"):
        fields = km.split_frontmatter((root / ticket["path"]).read_text())[0]
        record = rules.approval_record(fields)
        if record is not None:
            state = rules.approval_state(fields, km.spec_hash(path))
    with _TICKET_CACHE_LOCK:
        _TICKET_CACHE[folder] = (sig, ticket, record, state)
    return ticket, record, state


def board_tickets(conn, project: dict) -> list:
    """km.list_tickets semantics (same folders, same order) with an mtime cache; approval DB facts always live."""
    root = Path(project["root"])
    base = km.specs_dir(root)
    if not base.is_dir():
        return []
    folders = sorted(e.path for e in os.scandir(base) if e.is_dir() and not e.name.startswith(("_", ".")))
    tickets = []
    for folder in folders:
        ticket, record, state = _cached_ticket(root, folder)
        info = _approval_view(conn, project["id"], ticket, record, state)
        tickets.append({**ticket, "approval": info})  # shallow copy: the cached dict is never mutated
    return tickets


def build_board(conn, project: dict) -> dict:
    root = Path(project["root"])
    tickets = board_tickets(conn, project)
    return {"project": {"id": project["id"], "name": project["name"]}, "root": str(root), "editor": EDITOR_URL,
            "stages": [[s, km.STAGE_LABELS[s]] for s in km.STAGES], "tickets": tickets}


def specs_signature(root: Path) -> tuple:
    """(relative path, mtime_ns, size) of every .md under .SDD/specs — cheap change detection without libraries."""
    base = km.specs_dir(Path(root))
    out = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.endswith(".md"):
                path = os.path.join(folder, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                out.append((path, st.st_mtime_ns, st.st_size))
    return tuple(sorted(out))


# ---------------------------------------------------------------- daemon
class Daemon:
    def __init__(self, home: Path, tokens: dict, poll: float | None = None):
        self.home, self.tokens = home, tokens
        self.store = kdb.Store(kdb.db_path(home))
        self.store.conn()  # migrate now, before binding
        self.bus = EventBus()
        self.routes, self._staged, self._staged_hooks = [], [], []
        self.startup_hooks, self.human_move_hooks = [], []
        self.plugins = {"loaded": [], "failed": []}
        self.stopping = threading.Event()
        self.poll = poll if poll is not None else float(os.environ.get("KANBAN_POLL_SECONDS") or 1.0)
        self.signatures, self.sig_lock = {}, threading.Lock()
        self.move_locks, self.move_locks_lock = {}, threading.Lock()
        self.httpd = None
        self.ctx = PluginContext(self)
        self._core_routes()
        self._commit()

    # ---- plug-ins
    def _commit(self) -> None:
        self.routes.extend(self._staged)
        for kind, fn in self._staged_hooks:
            (self.startup_hooks if kind == "startup" else self.human_move_hooks).append(fn)
        self._staged, self._staged_hooks = [], []

    def load_plugins(self, dirs: list | None = None) -> None:
        if dirs is None:
            extra = [d for d in os.environ.get("KANBAN_ROUTES_DIRS", "").split(os.pathsep) if d]
            dirs = [SCRIPTS, *map(Path, extra)]
        files = sorted((p for d in dirs for p in Path(d).glob("routes_*.py") if p.is_file()), key=lambda p: p.name)
        for path in files:
            name = path.stem
            self.ctx._owner = name
            try:
                spec = importlib.util.spec_from_file_location(f"kanban_plugin_{name}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.register(self.ctx)
                self._commit()
                self.plugins["loaded"].append(name)
                log(f"plug-in {name} loaded")
            except Exception as exc:  # a broken plug-in never takes the board down
                self._staged, self._staged_hooks = [], []
                self.plugins["failed"].append(name)
                log(f"plug-in {name} skipped: {exc!r}\n{traceback.format_exc()}")
        self.ctx._owner = "core"

    def run_startup_hooks(self) -> None:
        for fn in self.startup_hooks:
            try:
                fn(self.ctx)
            except Exception as exc:
                log(f"on_startup hook {getattr(fn, '__qualname__', fn)} failed: {exc!r}")

    # ---- shared services
    def live_sessions(self, project_id: str, channel: bool | None = None) -> list:
        conn, out = self.store.conn(), []
        for sid in sorted(self.bus.session_ids()):
            session = kdb.get_session(conn, sid)
            if session and session["project_id"] == project_id and (channel is None
                                                                       or bool(session["channel"]) == channel):
                out.append(session)
        return out

    def record_approval(self, project_id: str, ticket: str, actor: str, session_id: str | None = None) -> dict:
        root = self.ctx.project(project_id)
        rec = kdb.record_approval(self.store.conn(), root, project_id, ticket, actor, session_id=session_id)
        self.bus.publish(project_id, "approval.recorded", {"ticket": ticket, "actor": actor, "id": rec["id"]})
        self.bus.publish(project_id, "board.changed", {"ticket": ticket, "source": "approval"})
        return rec

    def move_lock(self, project_id: str) -> threading.Lock:
        with self.move_locks_lock:
            return self.move_locks.setdefault(project_id, threading.Lock())

    def baseline(self, project: dict) -> None:
        with self.sig_lock:
            if project["id"] not in self.signatures:
                self.signatures[project["id"]] = specs_signature(Path(project["root"]))

    def watch(self) -> None:
        """board.changed when any .md under a registered project's .SDD/specs changes (mtime poll)."""
        while not self.stopping.wait(self.poll):
            try:
                projects = kdb.list_projects(self.store.conn())
            except Exception as exc:
                log(f"watcher: {exc!r}")
                continue
            for project in projects:
                sig = specs_signature(Path(project["root"]))
                with self.sig_lock:
                    old = self.signatures.get(project["id"])
                    self.signatures[project["id"]] = sig
                if old is not None and old != sig:
                    self.bus.publish(project["id"], "board.changed", {"source": "files"})

    def token_scope(self, header: str | None) -> str | None:
        if not header or not header.startswith("Bearer "):
            return None
        given = header[7:].strip().encode()
        for name, value in self.tokens.items():
            if hmac.compare_digest(given, value.encode()):
                return name
        return None

    def match(self, method: str, path: str):
        """(route, params) | (None, 'method') when only the method differs | (None, None)."""
        other = None
        for route in self.routes:
            m = route.regex.match(path)
            if m:
                if route.method == method:
                    return route, {k: urllib.parse.unquote(v) for k, v in m.groupdict().items()}
                other = "method"
        return None, other

    # ---- core routes
    def _core_routes(self) -> None:
        r = self.ctx.route
        r("GET", "/api/health", self.h_health, "read")
        r("GET", "/api/projects", self.h_projects, "read")
        r("POST", "/api/projects", self.h_add_project, "client")
        r("POST", "/api/sessions", self.h_session, "client")
        r("GET", "/api/events", self.h_events, "read")
        r("GET", "/api/projects/{project}/board", self.h_board, "read")
        r("GET", "/api/projects/{project}/view", self.h_view, "read")
        r("POST", "/api/projects/{project}/tickets/{ticket}/move", self.h_move, "ui")
        r("POST", "/api/projects/{project}/tickets/{ticket}/approve", self.h_approve, "ui")
        r("POST", "/api/projects/{project}/tickets/{ticket}/approve-chat", self.h_approve_chat, "client")
        r("POST", "/api/shutdown", self.h_shutdown, "client")

    def h_health(self, req):
        return {"api": API, "version": VERSION, "pid": os.getpid(), "live_runs": len(kdb.live_runs(req_conn(req))),
                "plugins": self.plugins, "subscribers": len(self.bus.subs)}

    def h_projects(self, req):
        return {"projects": [{"id": p["id"], "name": p["name"], "root": p["root"]}
                             for p in kdb.list_projects(req_conn(req))]}

    def register_project(self, conn, body: dict) -> dict:
        """Register (or touch) the body's project; a new one is announced to every board (project list refresh)."""
        root = project_root(body)
        known = kdb.get_project(conn, kdb.project_id_for(Path(root).resolve())) is not None
        project = kdb.register_project(conn, root)
        self.baseline(project)
        if not known:
            self.bus.publish(None, "project.registered", {"id": project["id"], "name": project["name"]})
        return project

    def h_add_project(self, req):
        project = self.register_project(req_conn(req), req.json)
        return {"project": {"id": project["id"], "name": project["name"], "root": project["root"]}}

    def h_session(self, req):
        body, conn = req.json, req_conn(req)
        project = self.register_project(conn, body)
        # kind and run_id are derived server-side (the body's are ignored): a session is headless only when its
        # claude process is the one a live headless run of this project recorded (pid + start time)
        run = headless_run_for(conn, project["id"], body.get("claude_pid"))
        kind, run_id = ("headless", run["id"]) if run else ("interactive", None)
        start = run["claude_start_time"] if run else body.get("claude_start_time", "")
        session = kdb.register_session(conn, project["id"], kind, body.get("claude_pid"), start,
                                       channel=bool(body.get("channel")) and not run, run_id=run_id)
        return {"session_id": session["id"], "project_id": project["id"], "kind": session["kind"], "api": API,
                "version": VERSION}

    def h_board(self, req):
        return build_board(req_conn(req), req.project())

    def h_view(self, req):
        project = req.project()
        root = Path(project["root"])
        rel = req.query.get("path", "")
        try:
            path = km.safe_path(root, rel)
        except ValueError as exc:
            raise ApiError(404, str(exc))
        if path.suffix != ".md" or not path.is_file():
            raise ApiError(404, "not a markdown file")
        return {"path": path.relative_to(root).as_posix(), "html": mdview.render_fragment(path, root),
                "editor": EDITOR_URL.format(path=str(path))}

    def h_move(self, req):
        project = req.project()
        ticket = req.ticket(project)
        pid, root = project["id"], Path(project["root"])
        dst = str(req.json.get("stage", "")).lower()
        if dst not in km.STAGES:
            raise ApiError(400, f"stage must be one of {', '.join(km.STAGES)}")
        actor = "human (board)"  # set server-side, never from the request
        folder = km.ticket_readme(root, ticket).parent
        with self.move_lock(pid), km.ticket_lock(folder):  # the ticket lock also excludes MCP kanban_move
            src = km.read_ticket(root, folder)["status"]
            try:
                moved = km.move_ticket(root, ticket, dst)
            except ValueError as exc:
                text = str(exc)
                reason = ("approval_required" if text.startswith("approval required") else
                          "spec_changed" if text.startswith("spec changed") else "invalid")
                raise ApiError(409, text, reason=reason)
            kind = rules.handoff_kind(src, dst, actor)
            handoff = kdb.create_handoff(req_conn(req), pid, ticket, kind, src, dst, actor) if kind else None
        self.bus.publish(pid, "board.changed", {"ticket": ticket, "from": src, "to": dst, "source": "move"})
        if handoff:
            self.bus.publish(pid, "handoff.created", {k: handoff[k] for k in (
                "id", "ticket", "kind", "stage", "from_stage", "status", "run_id", "superseded")})
        for fn in self.human_move_hooks:
            try:
                fn(pid, ticket, src, dst, handoff)
            except Exception as exc:
                log(f"on_human_move hook {getattr(fn, '__qualname__', fn)} failed: {exc!r}")
        return {"ok": True, "ticket": moved, "handoff": handoff}

    def h_approve(self, req):
        project = req.project()
        ticket = req.ticket(project)
        return {"ok": True, "approval": self.record_approval(project["id"], ticket, "human (board)")}

    def h_approve_chat(self, req):
        project = req.project()
        ticket = req.ticket(project)
        session = req.session
        if session is None:
            raise ApiError(403, "approve-chat needs a registered session (X-Kanban-Session)")
        if session["kind"] == "headless":
            raise ApiError(403, "a headless run cannot approve; the developer approves on the board or in chat")
        if session["project_id"] != project["id"]:
            raise ApiError(403, "the session belongs to another project")
        return {"ok": True, "approval": self.record_approval(project["id"], ticket, "human (chat)", session["id"])}

    def h_shutdown(self, req):
        conn = req_conn(req)
        live = kdb.live_runs(conn)
        if live and not req.json.get("cancel_runs"):
            raise ApiError(409, f"{len(live)} run(s) are live; stop with --cancel-runs to cancel them",
                           live_runs=len(live))
        for run in live:
            try:
                kdb.transition_run(conn, run["id"], "cancelled", reason="board service stopped")
            except (KeyError, ValueError):
                pass
        threading.Thread(target=self.shutdown, daemon=True).start()
        return {"ok": True, "cancelled": len(live)}

    def h_events(self, req):
        project = req.query.get("project") or None
        if project is not None and kdb.get_project(req_conn(req), project) is None:
            raise ApiError(404, "unknown project")
        session_id = req.session["id"] if req.session and req.scope == "client" else None
        sub = self.bus.subscribe(project, session_id)
        if sub is None:
            raise ApiError(503, f"too many event subscribers (max {MAX_SUBSCRIBERS})")
        h = req.handler
        try:
            h.send_response(200)
            h.send_common_headers("application/x-ndjson")
            h.end_headers()
            h.close_connection = True

            def write(ev):
                h.wfile.write((json.dumps(ev) + "\n").encode())
                h.wfile.flush()
            write({"id": 0, "event": "hello", "project": project, "data": {"session": session_id}, "at": time.time()})
            quiet = time.time()
            while not self.stopping.is_set() and not sub.overflow:
                try:
                    write(sub.queue.get(timeout=0.25))
                    quiet = time.time()
                except queue.Empty:
                    if time.time() - quiet > PING_SECONDS:
                        write({"id": 0, "event": "ping", "project": project, "data": {}, "at": time.time()})
                        quiet = time.time()
                if _peer_closed(h.connection):
                    break
        except OSError:
            pass
        finally:
            self.bus.unsubscribe(sub)
        return STREAMED

    # ---- lifecycle
    def shutdown(self) -> None:
        self.stopping.set()
        if self.httpd is not None:
            self.httpd.shutdown()


def req_conn(req):
    return req.daemon.store.conn()


# ---------------------------------------------------------------- HTTP handler
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "kanband"
    sys_version = ""
    daemon: Daemon = None  # set by serve()

    def log_message(self, *args):  # no access log: nothing sensitive, nothing noisy
        pass

    def send_common_headers(self, ctype: str) -> None:
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)

    def send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_common_headers(ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, code: int, obj, extra: dict | None = None) -> None:
        self.send(code, json.dumps(obj).encode(), "application/json", extra)

    def error(self, code: int, message: str, **extra) -> None:
        headers = {"WWW-Authenticate": "Bearer"} if code == 401 else None
        self.send_json(code, {"error": message, **extra}, headers)

    def _read_body(self) -> bytes | None:
        """Always consume the request body first, so a rejection never resets the connection."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            self.error(413, "request body too large")
            return None
        return self.rfile.read(length) if length else b""

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        port = self.server.server_address[1]
        return origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def do_GET(self):
        self.dispatch()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_GET

    def dispatch(self) -> None:
        body = self._read_body()
        if body is None:
            return
        if not self._host_ok() or not self._origin_ok():
            return self.error(403, "forbidden host or origin")
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if path == "/" or path == "/index.html" or path.startswith("/ui/"):
            if self.command not in ("GET", "HEAD"):
                return self.error(405, "method not allowed")
            return self.static(path)
        if not path.startswith("/api/"):
            return self.error(404, "not found")
        scope = self.daemon.token_scope(self.headers.get("Authorization"))
        if scope is None:
            return self.error(401, "missing or invalid token")
        method = self.command
        if _ui_only(method, path) and scope != "ui":
            return self.error(403, "this action needs the board's UI token")
        route, other = self.daemon.match(method, path)
        if route is None:
            return self.error(405 if other else 404, "method not allowed" if other else "not found")
        if route.scope not in TOKEN_SCOPES[scope]:
            return self.error(403, f"this endpoint needs the {route.scope} scope")
        conn = self.daemon.store.conn()
        sid = self.headers.get("X-Kanban-Session")
        session = kdb.get_session(conn, sid) if sid else None
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        req = Request(self.daemon, self, method, path, query, route_params(route, path), body, scope, session)
        try:
            result = route.handler(req)
        except ApiError as exc:
            return self.error(exc.status, exc.message, **exc.extra)
        except KeyError as exc:
            return self.error(404 if "no ticket" in str(exc) or "unknown" in str(exc) else 400,
                              str(exc).strip("'\""))
        except ValueError as exc:
            return self.error(400, str(exc))
        except Exception as exc:
            log(f"{method} {path} ({route.owner}) failed: {exc!r}\n{traceback.format_exc()}")
            return self.error(500, "internal error")
        if result is STREAMED:
            return
        if isinstance(result, tuple):
            return self.send_json(result[0], result[1])
        return self.send_json(200, result)

    def static(self, path: str) -> None:
        if path == "/ui/modules":
            folder = UI_DIR / "modules"
            names = sorted(p.name for p in folder.glob("*") if p.is_file()) if folder.is_dir() else []
            return self.send_json(200, {"js": [f"/ui/modules/{n}" for n in names if n.endswith(".js")],
                                        "css": [f"/ui/modules/{n}" for n in names if n.endswith(".css")]})
        rel = "index.html" if path in ("/", "/index.html") else urllib.parse.unquote(path[len("/ui/"):])
        target = (UI_DIR / rel).resolve()
        if UI_DIR not in target.parents or not target.is_file() or target.suffix not in CONTENT_TYPES:
            return self.error(404, "not found")
        self.send(200, target.read_bytes(), CONTENT_TYPES[target.suffix])


def route_params(route: Route, path: str) -> dict:
    m = route.regex.match(path)
    return {k: urllib.parse.unquote(v) for k, v in m.groupdict().items()} if m else {}


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


# ---------------------------------------------------------------- foreground run
def _bind(port: int | None) -> Server:
    if port is None:
        env = os.environ.get("KANBAN_DAEMON_PORT")
        candidates = [int(env)] if env else [DEFAULT_PORT, 0]
    else:
        candidates = [port]
    last = None
    for candidate in candidates:
        try:
            return Server(("127.0.0.1", candidate), Handler)
        except OSError as exc:
            last = exc
    raise last


def run_foreground(port: int | None = None) -> int:
    home = kdb.prepare_home()
    lock_fd = os.open(home / "daemon.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another kanban daemon holds daemon.lock; exiting")
        os.close(lock_fd)
        return 3
    try:
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, f"{os.getpid()}\n".encode())
        daemon = Daemon(home, ensure_tokens(home))
        daemon.load_plugins()
        httpd = _bind(port)
        Handler.daemon = daemon
        daemon.httpd = httpd
        info = {"port": httpd.server_address[1], "pid": os.getpid(), "api": API, "version": VERSION,
                "started_at": time.time()}
        daemon.run_startup_hooks()
        threading.Thread(target=daemon.watch, name="watcher", daemon=True).start()

        def stop(*_):
            threading.Thread(target=daemon.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        _write_private(home / "daemon.json", json.dumps(info) + "\n")
        log(f"serving {board_url(info)} (api {API}, version {VERSION})")
        httpd.serve_forever(poll_interval=0.2)
        daemon.stopping.set()
        current = read_info(home)
        if current and current.get("pid") == os.getpid():
            (home / "daemon.json").unlink()
        httpd.server_close()
        daemon.store.close()
        log("stopped")
        return 0
    finally:
        os.close(lock_fd)  # releases the flock


# ---------------------------------------------------------------- ensure / stop / open
class EnsureError(RuntimeError):
    pass


def _version_tuple(version) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(version or "0")))


def probe(home: Path, tokens: dict | None = None) -> dict | None:
    """The running daemon's health (+ port and url), or None."""
    info = read_info(home)
    if not info or not kdb.process_alive(info.get("pid")):
        return None
    tokens = tokens or ensure_tokens(home)
    try:
        status, health = api_request(info["port"], "GET", "/api/health", token=tokens["client"], timeout=2)
    except (OSError, KeyError, http.client.HTTPException):
        return None
    if status != 200 or not isinstance(health, dict) or health.get("pid") != info.get("pid"):
        return None
    return {**health, "port": info["port"], "url": board_url(info)}


def _lock_free(home: Path) -> bool:
    fd = os.open(home / "daemon.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _terminate(home: Path, pid: int, timeout: float = 5.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    end = time.time() + timeout
    while time.time() < end:
        if not kdb.process_alive(pid) or _lock_free(home):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _spawn(home: Path) -> subprocess.Popen:
    logfile = home / "daemon.log"
    try:
        if logfile.stat().st_size > 1024 * 1024:
            os.replace(logfile, home / "daemon.log.1")
    except OSError:
        pass
    fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        return subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--foreground"],
                                stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, close_fds=True,
                                start_new_session=True, cwd=str(home))
    finally:
        os.close(fd)


class _EnsureLock:
    def __init__(self, home: Path, timeout: float):
        self.path, self.timeout = home / "ensure.lock", timeout

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        end = time.time() + self.timeout
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK) or time.time() > end:
                    os.close(self.fd)
                    raise EnsureError("timed out waiting for another --ensure")
                time.sleep(0.05)

    def __exit__(self, *exc):
        os.close(self.fd)


def ensure(timeout: float = 10.0, home: Path | None = None) -> dict:
    """Reuse a healthy daemon with the same api; replace an idle older one; else start one. Returns its health plus
    port and url. Raises EnsureError when an api mismatch cannot be resolved (caller falls back to local mode)."""
    home = kdb.prepare_home(home)
    tokens = ensure_tokens(home)
    with _EnsureLock(home, timeout):
        info = probe(home, tokens)
        if info:
            same_api = info.get("api") == API
            older = _version_tuple(info.get("version")) < _version_tuple(VERSION)
            if older and not info.get("live_runs"):
                _terminate(home, int(info["pid"]))
            elif same_api:
                if older:
                    info["note"] = f"kept daemon {info.get('version')} while runs are live"
                return info
            else:
                raise EnsureError(f"the running board daemon speaks api {info.get('api')} (version "
                                  f"{info.get('version')}), this plugin needs api {API}")
        child = _spawn(home)
        end = time.time() + timeout
        while time.time() < end:
            info = probe(home, tokens)
            if info:
                if info.get("api") != API:
                    raise EnsureError(f"the board daemon speaks api {info.get('api')}, this plugin needs {API}")
                return info
            code = child.poll()
            if code not in (None, 3):  # 3: another daemon won the lock; keep waiting for it
                raise EnsureError(f"the board daemon exited with {code}; see {home / 'daemon.log'}")
            time.sleep(0.05)
        raise EnsureError(f"the board daemon did not answer within {timeout:.0f} s; see {home / 'daemon.log'}")


def stop(cancel_runs: bool = False, home: Path | None = None) -> tuple:
    """(exit code, message)."""
    home = kdb.prepare_home(home)
    tokens = ensure_tokens(home)
    info = probe(home, tokens)
    if not info:
        return 0, "The board daemon is not running."
    status, body = api_request(info["port"], "POST", "/api/shutdown", {"cancel_runs": cancel_runs},
                               token=tokens["client"])
    if status != 200:
        return 1, f"Refused: {(body or {}).get('error', status)}"
    end = time.time() + 10
    while time.time() < end and kdb.process_alive(info["pid"]) and not _lock_free(home):
        time.sleep(0.05)
    return 0, "Stopped the board daemon."


def register_project(info: dict, root: Path, home: Path) -> dict | None:
    status, body = api_request(info["port"], "POST", "/api/projects", {"project_root": str(root)},
                               token=ensure_tokens(home)["client"])
    return body if status == 200 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kanban board daemon (one per user).")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ensure", action="store_true", help="start it if needed and print the board URL")
    mode.add_argument("--open", action="store_true", help="ensure, then open the board in the browser")
    mode.add_argument("--stop", action="store_true", help="stop it (refused while runs are live)")
    mode.add_argument("--foreground", action="store_true", help="run the daemon in this process")
    ap.add_argument("--cancel-runs", action="store_true", help="with --stop: cancel live runs")
    ap.add_argument("--port", type=int, default=None, help="with --foreground: port (default 47821, else any)")
    ap.add_argument("--project", default=None, help="with --open: project folder to add to the board")
    args = ap.parse_args(argv)
    if args.foreground:
        return run_foreground(args.port)
    if args.stop:
        code, message = stop(args.cancel_runs)
        print(message)
        return code
    try:
        info = ensure()
    except EnsureError as exc:
        print(f"kanban: {exc}", file=sys.stderr)
        return 1
    if args.open:
        home = kdb.prepare_home()
        root = Path(args.project).resolve() if args.project else km.project_dir()
        if args.project or (root / ".SDD").is_dir():
            register_project(info, root, home)
        import webbrowser
        webbrowser.open(ui_url(info, home))
        print(f"Opened the board: {info['url']}")
        return 0
    print(info["url"] + (f"  ({info['note']})" if info.get("note") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
