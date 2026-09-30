"""Operational store of the kanban daemon (architecture §2, ADR-002): SQLite at <KANBAN_HOME>/kanban.db.

Markdown stays the source of truth for tickets; this store holds only process facts and history: the project registry,
MCP sessions, hand-offs, runs and their events, approvals with a snapshot of the approved spec, the Claude session per
ticket and settings. It owns every migration of the feature (PRAGMA user_version). Deleting the file loses history
only: it is recreated on the next connection and project ids are derived from the project path.

WAL, busy_timeout 5000 ms, one connection per thread (Store), autocommit with explicit BEGIN IMMEDIATE for claims.
Standard library only.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import kanban_md as km
import kanban_rules as rules

SCHEMA_VERSION = 1
DB_NAME = "kanban.db"
RUN_STATES = ("queued", "running", "waiting", "succeeded", "failed", "cancelled", "abandoned")
RUN_LIVE = ("queued", "running", "waiting")
RUN_TERMINAL = ("succeeded", "failed", "cancelled", "abandoned")
RUN_TRANSITIONS = {
    "queued": {"running", "failed", "cancelled", "abandoned"},
    "running": {"waiting", "succeeded", "failed", "cancelled", "abandoned"},
    "waiting": {"running", "succeeded", "failed", "cancelled", "abandoned"},
}
RUN_KINDS = ("interactive", "headless")
SESSION_KINDS = ("interactive", "headless")
SESSION_ORIGINS = ("cli", "cli-print", "code-tab", "headless", "unknown")
SESSION_PYTHONS = ("bundled", "system", "unknown")
HANDOFF_KINDS = ("start", "rework")
HANDOFF_STATES = ("queued", "delivered", "claimed", "done", "coalesced", "superseded", "requeued")
HANDOFF_UNCLAIMED = ("queued", "delivered", "coalesced", "requeued")
HANDOFF_CLAIMABLE = ("queued", "delivered", "requeued")

_LIVE_SQL = "('queued', 'running', 'waiting')"
MIGRATIONS = {
    1: f"""
CREATE TABLE projects (
    id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    created_at REAL NOT NULL, last_seen REAL NOT NULL);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL CHECK (kind IN ('interactive', 'headless')), channel INTEGER NOT NULL DEFAULT 0,
    claude_pid INTEGER, claude_start_time TEXT, run_id TEXT, created_at REAL NOT NULL, last_seen REAL NOT NULL);
CREATE TABLE handoffs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, ticket TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('start', 'rework')), stage TEXT NOT NULL, from_stage TEXT NOT NULL,
    actor TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'delivered', 'claimed', 'done', 'coalesced', 'superseded',
                                           'requeued')),
    run_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE INDEX handoffs_ticket ON handoffs(project_id, ticket, status);
CREATE TABLE runs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, ticket TEXT NOT NULL, stage TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('interactive', 'headless')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled',
                                           'abandoned')),
    handoff_id TEXT, session_id TEXT, claude_pid INTEGER, claude_start_time TEXT, pid INTEGER,
    claude_session_id TEXT, reason TEXT, note TEXT,
    created_at REAL NOT NULL, started_at REAL, heartbeat_at REAL, ended_at REAL);
CREATE UNIQUE INDEX runs_one_live_per_ticket ON runs(project_id, ticket) WHERE status IN {_LIVE_SQL};
CREATE TABLE run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, at REAL NOT NULL, type TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{{}}');
CREATE INDEX run_events_run ON run_events(run_id, id);
CREATE TABLE approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, ticket TEXT NOT NULL, actor TEXT NOT NULL,
    at REAL NOT NULL, date TEXT NOT NULL, hash TEXT NOT NULL, session_id TEXT);
CREATE INDEX approvals_ticket ON approvals(project_id, ticket, id);
CREATE TABLE approval_files (
    approval_id INTEGER NOT NULL REFERENCES approvals(id), path TEXT NOT NULL, content TEXT NOT NULL,
    PRIMARY KEY (approval_id, path));
CREATE TABLE ticket_sessions (
    project_id TEXT NOT NULL, ticket TEXT NOT NULL, claude_session_id TEXT NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY (project_id, ticket));
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
""",
}


# Additive columns (ADR-004): added after `migrate` when missing, outside the user_version ladder, so a v0.3.0 daemon
# (SCHEMA_VERSION 1) still opens the file and its INSERTs (which name no such column) get the default. A later real
# migration must tolerate these columns already existing.
ADDITIVE_COLUMNS = (("sessions", "origin", "TEXT NOT NULL DEFAULT 'unknown'"),
                    ("sessions", "python", "TEXT NOT NULL DEFAULT 'unknown'"))


class LiveRunExists(ValueError):
    """The ticket already has a queued, running or waiting run (partial unique index)."""


# ---------------------------------------------------------------- home and connections
def default_home() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Kanban"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "kanban"


# ---------------------------------------------------------------- processes (the one pid + start time helper)
def _pid(pid) -> int | None:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def process_start_time(pid) -> str:
    """The start time `ps` reports for pid ('' if unknown): with the pid it identifies a process across pid reuse."""
    value = _pid(pid)
    if value is None:
        return ""
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(value)], capture_output=True, text=True, timeout=2,
                             env={**os.environ, "LC_ALL": "C"})
        return " ".join(out.stdout.split())
    except (OSError, subprocess.SubprocessError):
        return ""


def process_alive(pid, start: str | None = None) -> bool:
    """pid exists (a permission error still means it exists); with `start`, it must also be the same process — an
    unknown start time (ps failed) counts as the same, so a transient ps failure never abandons a run."""
    value = _pid(pid)
    if value is None:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    want = " ".join(str(start or "").split())
    return not want or process_start_time(value) in ("", want)


def kanban_home() -> Path:
    """KANBAN_HOME, else the platform default (not created here)."""
    value = os.environ.get("KANBAN_HOME")
    return Path(value).expanduser() if value else default_home()


def prepare_home(home: Path | None = None) -> Path:
    """Create KANBAN_HOME (0700) and return it."""
    home = Path(home) if home else kanban_home()
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    return home


def db_path(home: Path | None = None) -> Path:
    return Path(home or kanban_home()) / DB_NAME


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"kanban.db has schema {version}; this version understands up to {SCHEMA_VERSION}")
    for target in range(version + 1, SCHEMA_VERSION + 1):
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] < target:  # another process may have migrated
                for statement in MIGRATIONS[target].split(";\n"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {target}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def add_columns(conn) -> None:
    """Add every ADDITIVE_COLUMNS entry the table lacks (idempotent; a concurrent start that added it first makes
    ALTER fail with "duplicate column", which is tolerated)."""
    for table, column, decl in ADDITIVE_COLUMNS:
        if column in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    # One-time setup is serialised across threads and processes: switching a fresh file to WAL needs an exclusive
    # lock that SQLite may refuse at once ("database is locked") instead of waiting, when several sessions start
    # together (reproduced 5/40 with 8 concurrent openers before this lock).
    lock_fd = os.open(str(path) + ".init.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
        add_columns(conn)
    finally:
        os.close(lock_fd)  # releases the flock
    for suffix in ("", "-wal", "-shm"):
        try:
            os.chmod(str(path) + suffix, 0o600)
        except OSError:
            pass
    return conn


class Store:
    """Thread-local connection factory: `store.conn()` returns this thread's connection."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self._all = []
        self._lock = threading.Lock()

    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect(self.path)
            self._local.conn = conn
            with self._lock:
                # the daemon serves each request on a new thread: close the connections of threads that ended, or
                # they pile up until the process hits its file limit ("unable to open database file")
                alive = []
                for thread, old in self._all:
                    if thread.is_alive():
                        alive.append((thread, old))
                    else:
                        try:
                            old.close()
                        except sqlite3.Error:
                            pass
                alive.append((threading.current_thread(), conn))
                self._all = alive
        return conn

    __call__ = conn

    def close(self) -> None:
        with self._lock:
            for _, conn in self._all:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._all.clear()
        self._local = threading.local()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE … COMMIT (or ROLLBACK); nested use joins the outer transaction."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _row(row) -> dict | None:
    return dict(row) if row is not None else None


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


# ---------------------------------------------------------------- projects and sessions
def project_id_for(root) -> str:
    return hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:12]


def register_project(conn, root) -> dict:
    if not Path(str(root)).is_absolute():
        raise ValueError("project_root must be an absolute path")
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"project_root {root} is not a directory")
    pid, now = project_id_for(root), time.time()
    conn.execute("INSERT INTO projects (id, root, name, created_at, last_seen) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen", (pid, str(root), root.name, now, now))
    return get_project(conn, pid)


def get_project(conn, project_id: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM projects WHERE id=?", (str(project_id),)).fetchone())


def list_projects(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY name, id")]


def register_session(conn, project_id: str, kind: str, claude_pid, claude_start_time, channel: bool = False,
                     run_id: str | None = None, origin: str = "unknown", python: str = "unknown") -> dict:
    """origin: how the session is connected (SESSION_ORIGINS, a label only); python: the interpreter its MCP server
    runs on (SESSION_PYTHONS)."""
    if kind not in SESSION_KINDS:
        raise ValueError(f"kind must be one of {', '.join(SESSION_KINDS)}")
    if origin not in SESSION_ORIGINS:
        raise ValueError(f"origin must be one of {', '.join(SESSION_ORIGINS)}")
    if python not in SESSION_PYTHONS:
        raise ValueError(f"python must be one of {', '.join(SESSION_PYTHONS)}")
    sid, now = _new_id("s"), time.time()
    conn.execute("INSERT INTO sessions (id, project_id, kind, channel, claude_pid, claude_start_time, run_id, "
                 "created_at, last_seen, origin, python) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (sid, project_id, kind, 1 if channel else 0, int(claude_pid) if claude_pid else None,
                  str(claude_start_time or ""), run_id, now, now, origin, python))
    return get_session(conn, sid)


def remove_project(conn, project_id: str) -> list[str]:
    """Delete the project row and its session rows (hand-offs, runs, approvals stay: re-adding the same folder yields
    the same id and reattaches them). Returns the removed session ids."""
    with transaction(conn):
        sids = [r[0] for r in conn.execute("SELECT id FROM sessions WHERE project_id=?", (str(project_id),))]
        conn.execute("DELETE FROM sessions WHERE project_id=?", (str(project_id),))
        conn.execute("DELETE FROM projects WHERE id=?", (str(project_id),))
    return sids


def get_session(conn, session_id: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM sessions WHERE id=?", (str(session_id),)).fetchone())


# ---------------------------------------------------------------- runs
def run_transition_allowed(src: str, dst: str) -> bool:
    """Terminal states are sticky: nothing leaves succeeded, failed, cancelled or abandoned."""
    return dst in RUN_TRANSITIONS.get(src, ())


def _event(conn, run_id: str, kind: str, data: dict) -> None:
    conn.execute("INSERT INTO run_events (run_id, at, type, data) VALUES (?, ?, ?, ?)",
                 (run_id, time.time(), kind, json.dumps(data)))


def create_run(conn, project_id: str, ticket: str, stage: str, kind: str, status: str = "queued",
               handoff_id: str | None = None, session_id: str | None = None, run_id: str | None = None,
               claude_pid=None, claude_start_time: str | None = None) -> dict:
    if kind not in RUN_KINDS:
        raise ValueError(f"kind must be one of {', '.join(RUN_KINDS)}")
    if status not in RUN_LIVE:
        raise ValueError("a new run starts queued, running or waiting")
    run_id, now = run_id or _new_id("r"), time.time()
    started = None if status == "queued" else now
    try:
        with transaction(conn):
            conn.execute("INSERT INTO runs (id, project_id, ticket, stage, kind, status, handoff_id, session_id, "
                         "claude_pid, claude_start_time, created_at, started_at, heartbeat_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (run_id, project_id, ticket, rules._stage(stage), kind, status, handoff_id, session_id,
                          claude_pid, claude_start_time, now, started, started))
            _event(conn, run_id, "status", {"from": None, "to": status})
    except sqlite3.IntegrityError as exc:
        raise LiveRunExists(f"ticket {ticket} already has a live run") from exc
    return get_run(conn, run_id)


def get_run(conn, run_id: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM runs WHERE id=?", (str(run_id),)).fetchone())


def live_run(conn, project_id: str, ticket: str) -> dict | None:
    return _row(conn.execute(f"SELECT * FROM runs WHERE project_id=? AND ticket=? AND status IN {_LIVE_SQL}",
                             (project_id, ticket)).fetchone())


def live_runs(conn, project_id: str | None = None) -> list[dict]:
    if project_id is None:
        return [dict(r) for r in conn.execute(f"SELECT * FROM runs WHERE status IN {_LIVE_SQL} ORDER BY created_at")]
    return [dict(r) for r in conn.execute(f"SELECT * FROM runs WHERE project_id=? AND status IN {_LIVE_SQL} "
                                          "ORDER BY created_at", (project_id,))]


def transition_run(conn, run_id: str, status: str, reason: str | None = None) -> dict:
    """Move a run to `status` (RUN_TRANSITIONS; terminal states are sticky), log a run event, requeue on finish."""
    if status not in RUN_STATES:
        raise ValueError(f"status must be one of {', '.join(RUN_STATES)}")
    with transaction(conn):
        run = get_run(conn, run_id)
        if run is None:
            raise KeyError(f"no run {run_id}")
        if not run_transition_allowed(run["status"], status):
            raise ValueError(f"run {run_id} cannot go from {run['status']} to {status}")
        now = time.time()
        terminal = status in RUN_TERMINAL
        conn.execute("UPDATE runs SET status=?, reason=COALESCE(?, reason), heartbeat_at=?, "
                     "started_at=COALESCE(started_at, ?), ended_at=? WHERE id=?",
                     (status, reason, now, now if status == "running" else None, now if terminal else None, run_id))
        _event(conn, run_id, "status", {"from": run["status"], "to": status, "reason": reason})
        if terminal:
            conn.execute("UPDATE handoffs SET status='done', updated_at=? WHERE run_id=? AND status='claimed'",
                         (now, run_id))
            requeue(conn, run_id)
    return get_run(conn, run_id)


def run_events(conn, run_id: str) -> list[dict]:
    return [{**dict(r), "data": json.loads(r["data"])}
            for r in conn.execute("SELECT * FROM run_events WHERE run_id=? ORDER BY id", (run_id,))]


# ---------------------------------------------------------------- hand-offs
def get_handoff(conn, handoff_id: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM handoffs WHERE id=?", (str(handoff_id),)).fetchone())


def pending_handoffs(conn, project_id: str, ticket: str | None = None) -> list[dict]:
    """Claimable hand-offs (queued, delivered, requeued), oldest first."""
    sql = "SELECT * FROM handoffs WHERE project_id=? AND status IN ('queued', 'delivered', 'requeued')"
    args = [project_id]
    if ticket is not None:
        sql += " AND ticket=?"
        args.append(ticket)
    return [dict(r) for r in conn.execute(sql + " ORDER BY created_at", args)]


def create_handoff(conn, project_id: str, ticket: str, kind: str, from_stage: str, stage: str, actor: str) -> dict:
    """Insert a hand-off for a human board move: older unclaimed hand-offs of the ticket are superseded; a target
    equal to the live run's stage is coalesced into that run. Returns the row plus `superseded` (ids)."""
    if kind not in HANDOFF_KINDS:
        raise ValueError(f"kind must be one of {', '.join(HANDOFF_KINDS)}")
    rules._stage(stage)
    rules._stage(from_stage)
    hid, now = _new_id("h"), time.time()
    marks = ", ".join("?" * len(HANDOFF_UNCLAIMED))
    with transaction(conn):
        old = [r[0] for r in conn.execute(f"SELECT id FROM handoffs WHERE project_id=? AND ticket=? AND status IN "
                                          f"({marks}) ORDER BY created_at", (project_id, ticket, *HANDOFF_UNCLAIMED))]
        conn.executemany("UPDATE handoffs SET status='superseded', updated_at=? WHERE id=?", [(now, i) for i in old])
        live = live_run(conn, project_id, ticket)
        status, run_id = ("coalesced", live["id"]) if live and live["stage"] == stage else ("queued", None)
        conn.execute("INSERT INTO handoffs (id, project_id, ticket, kind, stage, from_stage, actor, status, run_id, "
                     "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (hid, project_id, ticket, kind, stage, from_stage, actor, status, run_id, now, now))
    return {**get_handoff(conn, hid), "superseded": old}


def mark_delivered(conn, handoff_id: str) -> None:
    conn.execute("UPDATE handoffs SET status='delivered', updated_at=? WHERE id=? AND status IN ('queued', 'requeued')",
                 (time.time(), handoff_id))


def claim(conn, handoff_id: str, run_id: str | None = None, session_id: str | None = None,
          kind: str = "interactive", claude_pid=None, claude_start_time: str | None = None) -> tuple:
    """First claim wins: ('ok', run_id) | ('already_claimed', None) | ('superseded', None) | ('not_found', None).
    The run that already owns the claim gets ('ok', run_id) again (idempotent)."""
    with transaction(conn):
        h = get_handoff(conn, handoff_id)
        if h is None:
            return "not_found", None
        if h["status"] == "superseded":
            return "superseded", None
        if h["status"] not in HANDOFF_CLAIMABLE:  # claimed, done or coalesced into a live run
            return ("ok", h["run_id"]) if run_id and run_id == h["run_id"] else ("already_claimed", None)
        live = live_run(conn, h["project_id"], h["ticket"])
        if live:
            if not run_id or live["id"] != run_id:
                return "already_claimed", None
            conn.execute("UPDATE runs SET stage=?, handoff_id=?, session_id=COALESCE(?, session_id) WHERE id=?",
                         (h["stage"], h["id"], session_id, run_id))
            if live["status"] != "running":
                transition_run(conn, run_id, "running")
        else:
            run_id = create_run(conn, h["project_id"], h["ticket"], h["stage"], kind, status="running",
                                handoff_id=h["id"], session_id=session_id, run_id=run_id, claude_pid=claude_pid,
                                claude_start_time=claude_start_time)["id"]
        conn.execute("UPDATE handoffs SET status='claimed', run_id=?, updated_at=? WHERE id=?",
                     (run_id, time.time(), h["id"]))
    return "ok", run_id


def requeue(conn, run_id: str) -> list[str]:
    """After a run finished: its coalesced hand-offs for another stage are requeued, the others are done."""
    run = get_run(conn, run_id)
    if run is None:
        return []
    now, again = time.time(), []
    for h in conn.execute("SELECT * FROM handoffs WHERE run_id=? AND status='coalesced'", (run_id,)).fetchall():
        if h["stage"] != run["stage"]:
            conn.execute("UPDATE handoffs SET status='requeued', run_id=NULL, updated_at=? WHERE id=?", (now, h["id"]))
            again.append(h["id"])
        else:
            conn.execute("UPDATE handoffs SET status='done', updated_at=? WHERE id=?", (now, h["id"]))
    return again


# ---------------------------------------------------------------- approvals
def record_approval(conn, root, project_id: str, ticket: str, actor: str, session_id: str | None = None) -> dict:
    """The single writer of an approval: km.approve (README + approval line), an approvals row and a snapshot of the
    spec files it covers (for the diff)."""
    if actor not in rules.ACTORS:
        raise ValueError(f"actor must be one of {', '.join(rules.ACTORS)}")
    root = Path(root)
    result = km.approve(root, ticket, actor)
    files = km.spec_texts(km.ticket_readme(root, ticket).parent)
    with transaction(conn):
        cur = conn.execute("INSERT INTO approvals (project_id, ticket, actor, at, date, hash, session_id) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (project_id, ticket, actor, time.time(), result["approved_at"], result["approved_hash"],
                            session_id))
        approval_id = cur.lastrowid
        conn.executemany("INSERT INTO approval_files (approval_id, path, content) VALUES (?, ?, ?)",
                         [(approval_id, name, text) for name, text in sorted(files.items())])
    return {**result, "id": approval_id, "actor": actor, "project_id": project_id}


def latest_approval(conn, project_id: str, ticket: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM approvals WHERE project_id=? AND ticket=? ORDER BY id DESC LIMIT 1",
                             (project_id, ticket)).fetchone())


def approval_files(conn, approval_id: int) -> dict:
    return {r["path"]: r["content"]
            for r in conn.execute("SELECT path, content FROM approval_files WHERE approval_id=?", (approval_id,))}


# ---------------------------------------------------------------- ticket sessions and settings
def set_ticket_session(conn, project_id: str, ticket: str, claude_session_id: str) -> None:
    conn.execute("INSERT INTO ticket_sessions (project_id, ticket, claude_session_id, updated_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(project_id, ticket) DO UPDATE SET claude_session_id=excluded.claude_session_id, "
                 "updated_at=excluded.updated_at", (project_id, ticket, claude_session_id, time.time()))


def get_ticket_session(conn, project_id: str, ticket: str) -> str | None:
    row = conn.execute("SELECT claude_session_id FROM ticket_sessions WHERE project_id=? AND ticket=?",
                       (project_id, ticket)).fetchone()
    return row[0] if row else None


class Settings:
    """Key/value settings in the settings table (values are stored as text)."""

    def __init__(self, store: Store):
        self._store = store

    def get(self, key: str, default=None):
        row = self._store.conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value) -> None:
        self._store.conn().execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                                   "value=excluded.value", (key, str(value)))

    def delete(self, key: str) -> None:
        self._store.conn().execute("DELETE FROM settings WHERE key=?", (key,))

    def items(self) -> dict:
        return {r[0]: r[1] for r in self._store.conn().execute("SELECT key, value FROM settings ORDER BY key")}
