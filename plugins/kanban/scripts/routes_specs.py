"""Daemon plug-in (PRD-05; architecture §3.1, §7, §8): spec editor, diff since approval and a read-only git panel.

    GET /api/projects/{project}/files?path=<rel>   read   {path, ticket, content, sha256}
    PUT /api/projects/{project}/files?path=<rel>   ui     body {"content": …}, header If-Match: <sha256 of current
                                                          bytes>; 428 without it, 409 when stale or when a protected
                                                          frontmatter key (status, approved_*) would change
    GET /api/projects/{project}/diff/{ticket}      read   unified diff of the spec files since the latest approval
                                                          snapshot (approval null when none), current hash and files
    GET /api/projects/{project}/git[?ticket=<id>]  read   branch, staged/changed/untracked files and the git block of
                                                          the latest handoff-note.md (the ticket's, else the project's)

Paths are confined to .SDD/specs/**.md after resolving symlinks (kanban_md.safe_path); clients never send roots.
Git runs as argv lists without a shell, with a timeout, and only the two read-only commands of architecture §8.
Standard library only.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import kanban_db as kdb
import kanban_md as km
import kanban_rules as rules

PROTECTED_KEYS = ("status", "approved_by", "approved_at", "approved_hash")
TICKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GIT_TIMEOUT = 5.0
_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")
_WRITE_LOCK = threading.Lock()  # If-Match check and write are one step for concurrent PUTs


# ---------------------------------------------------------------- helpers
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def protected_fields(text: str) -> list:
    """(key, value) of every protected frontmatter line, in order (duplicates and removals count as changes)."""
    raw = km.split_frontmatter(text)[1]
    out = []
    for line in raw:
        m = _KEY_RE.match(line)
        if m and m.group(1) in PROTECTED_KEYS:
            out.append((m.group(1), m.group(2).strip()))
    return out


def write_atomic(path: Path, data: bytes) -> None:
    """Temp file in the same folder, fsync, rename over the target; keeps the target's permission bits."""
    mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def resolve_spec(ctx, root: Path, rel: str) -> Path:
    """An existing .md file inside .SDD/specs (symlinks resolved), or ApiError."""
    if not rel:
        raise ctx.ApiError(400, "path is required")
    try:
        path = km.safe_path(root, rel)
    except (ValueError, OSError) as exc:
        raise ctx.ApiError(403, str(exc) if isinstance(exc, ValueError) else "invalid path")
    if path.suffix != ".md":
        raise ctx.ApiError(403, "only markdown files under .SDD/specs can be opened")
    if not path.is_file():
        raise ctx.ApiError(404, "no such markdown file")
    return path


def ticket_of(root: Path, path: Path) -> str | None:
    parts = path.relative_to(km.specs_dir(root).resolve()).parts
    return parts[0] if len(parts) > 1 else None


def check_ticket(ctx, root: Path, ticket: str) -> Path:
    if not TICKET_RE.match(ticket or "") or ".." in ticket:
        raise ctx.ApiError(400, "invalid ticket id")
    try:
        return km.ticket_readme(root, ticket).parent
    except KeyError:
        raise ctx.ApiError(404, f"no ticket {ticket}")


# ---------------------------------------------------------------- diff
def spec_diff(snapshot: dict, current: dict) -> tuple:
    """(unified diff text, names whose content differs) between two {name: text} maps."""
    chunks, changed = [], []
    for name in sorted(set(snapshot) | set(current)):
        old, new = snapshot.get(name), current.get(name)
        if old == new:
            continue
        changed.append(name)
        chunks.extend(difflib.unified_diff(
            (old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True),
            fromfile=f"a/{name}" if old is not None else "/dev/null",
            tofile=f"b/{name}" if new is not None else "/dev/null"))
        if chunks and not chunks[-1].endswith("\n"):
            chunks[-1] += "\n"
    return "".join(chunks), changed


# ---------------------------------------------------------------- git (read-only)
def parse_porcelain(text: str) -> dict:
    staged, changed, untracked = [], [], []
    for line in text.splitlines():
        if len(line) < 4:
            continue
        x, y, path = line[0], line[1], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        if x == "?" and y == "?":
            untracked.append(path)
            continue
        if x == "!":
            continue
        if x != " ":
            staged.append(path)
        if y != " ":
            changed.append(path)
    return {"staged": staged, "changed": changed, "untracked": untracked}


def _head_file_branch(root: Path) -> str | None:
    """Branch of a repository without commits (rev-parse cannot name it): read .git/HEAD, no git process."""
    try:
        head = (Path(root) / ".git" / "HEAD").read_text().strip()
    except OSError:
        return None
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


def git_status(root, run=subprocess.run) -> dict:
    """Branch and file states from exactly the two read-only git commands of architecture §8."""
    root = str(root)
    result = {"branch": None, "staged": [], "changed": [], "untracked": [], "error": None}
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    common = {"capture_output": True, "text": True, "timeout": GIT_TIMEOUT, "stdin": subprocess.DEVNULL, "env": env}
    try:
        status = run(["git", "--no-optional-locks", "-c", "core.fsmonitor=", "-C", root, "status", "--porcelain=v1"],
                     **common)
        if status.returncode != 0:
            result["error"] = "git status failed: " + ((status.stderr or "").strip().splitlines() or ["?"])[0]
            return result
        result.update(parse_porcelain(status.stdout or ""))
        branch = run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"], **common)
        if branch.returncode == 0:
            result["branch"] = (branch.stdout or "").strip() or None
        else:
            result["branch"] = _head_file_branch(Path(root))
    except FileNotFoundError:
        result["error"] = "git is not installed"
    except subprocess.TimeoutExpired:
        result["error"] = f"git did not answer within {GIT_TIMEOUT:.0f} s"
    except OSError as exc:
        result["error"] = f"git could not run: {exc.strerror or exc}"
    return result


def git_block(text: str) -> str | None:
    """The first fenced code block under the '## Git' heading of a hand-off note."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"^##\s+git\b", l, re.I)), None)
    if start is None:
        return None
    block, inside = [], False
    for line in lines[start + 1:]:
        if not inside and line.startswith("## "):
            break
        if re.match(r"^\s*(```|~~~)", line):
            if inside:
                return "\n".join(block)
            inside = True
            continue
        if inside:
            block.append(line)
    return "\n".join(block) if inside else None


def latest_handoff(root: Path, folder: Path) -> dict | None:
    base = km.specs_dir(root).resolve()
    best = None
    for path in folder.glob("**/*handoff-note*.md"):
        try:
            real = path.resolve()
            if base not in real.parents or not real.is_file():
                continue
            mtime = real.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, path, real)
    if best is None:
        return None
    text = best[2].read_text(errors="replace")
    return {"path": best[1].relative_to(root).as_posix(), "git_block": git_block(text), "updated": best[0]}


# ---------------------------------------------------------------- routes
def register(ctx):
    def project(req):
        found = req.project()
        return found["id"], Path(found["root"])

    def get_file(req):
        pid, root = project(req)
        path = resolve_spec(ctx, root, req.query.get("path", ""))
        data = path.read_bytes()
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ctx.ApiError(415, "the file is not UTF-8 text")
        return {"path": path.relative_to(root.resolve()).as_posix(), "ticket": ticket_of(root, path),
                "content": content, "sha256": sha256(data)}

    def put_file(req):
        pid, root = project(req)
        path = resolve_spec(ctx, root, req.query.get("path", ""))
        content = req.json.get("content")
        if not isinstance(content, str):
            raise ctx.ApiError(400, "body must be {\"content\": <text>}")
        expected = (req.headers.get("If-Match") or "").strip()
        if expected.startswith("W/"):
            expected = expected[2:]
        expected = expected.strip('"').lower()
        if not expected:
            raise ctx.ApiError(428, "If-Match: <sha256 of the current content> is required")
        rel = path.relative_to(root.resolve()).as_posix()
        new = content.encode("utf-8")
        with _WRITE_LOCK:
            current = path.read_bytes()
            if sha256(current) != expected:
                raise ctx.ApiError(409, "the file changed since it was loaded; reload it", reason="stale",
                                   sha256=sha256(current))
            if protected_fields(current.decode("utf-8", errors="replace")) != protected_fields(content):
                raise ctx.ApiError(409, "status and approved_* cannot be edited here; use move/approve",
                                   reason="protected", keys=list(PROTECTED_KEYS))
            if new != current:
                write_atomic(path, new)
        ticket = ticket_of(root, path)
        if new != current:
            ctx.publish(pid, "board.changed", {"ticket": ticket, "path": rel, "source": "edit"})
        return {"ok": True, "path": rel, "ticket": ticket, "sha256": sha256(new)}

    def get_diff(req):
        pid, root = project(req)
        ticket = req.params.get("ticket", "")
        folder = check_ticket(ctx, root, ticket)
        current = km.spec_texts(folder)
        digest = rules.spec_hash_from_texts(current)
        latest = kdb.latest_approval(ctx.db(), pid, ticket)
        out = {"ticket": ticket, "hash": digest, "files": sorted(current), "approval": None, "changed": False,
               "changed_files": [], "diff": ""}
        if latest is None:
            return out
        diff, changed = spec_diff(kdb.approval_files(ctx.db(), latest["id"]), current)
        out.update(approval={k: latest[k] for k in ("id", "actor", "at", "date", "hash")},
                   changed=latest["hash"] != digest, changed_files=changed, diff=diff)
        return out

    def get_git(req):
        pid, root = project(req)
        ticket = req.query.get("ticket")
        folder = check_ticket(ctx, root, ticket) if ticket is not None else km.specs_dir(root)
        result = git_status(root)
        result["handoff"] = latest_handoff(root, folder) if folder.is_dir() else None
        return result

    ctx.route("GET", "/api/projects/{project}/files", get_file, "read")
    ctx.route("PUT", "/api/projects/{project}/files", put_file, "ui")
    ctx.route("GET", "/api/projects/{project}/diff/{ticket}", get_diff, "read")
    ctx.route("GET", "/api/projects/{project}/git", get_git, "read")
