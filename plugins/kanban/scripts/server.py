#!/usr/bin/env python3
"""Kanban MCP server (stdio JSON-RPC 2.0, newline-delimited) + a local web board over .SDD/specs markdown.

Claude Code starts it through the plugin's .mcp.json. The markdown files are the source of truth; this server only
reads and edits them (status frontmatter, checkboxes, new ticket/sub-task files). In a background thread it serves the
board at http://127.0.0.1:<port>/ (port per project, written to .kanban/url). `server.py --ui` serves only the board.
Standard library only.
"""
from __future__ import annotations

import hashlib
import html
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import urllib.parse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kanban_md as km  # noqa: E402

VERSION = "0.2.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
ROOT = km.project_dir()
UI_FILE = Path(__file__).with_name("ui.html")
EDITOR_URL = os.environ.get("KANBAN_EDITOR_URL", "vscode://file/{path}")
STAGE = {"type": "string", "enum": list(km.STAGES)}

TOOLS = [
    {"name": "kanban_board",
     "description": "Show all tickets (.SDD/specs/<slug>/) by ADLC stage, with sub-tasks, checkbox progress, file paths "
                    "and the web board URL.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "kanban_new_ticket",
     "description": "Create a ticket = a new spec folder .SDD/specs/<slug>/README.md (status: discovery, Progress "
                    "checklist). Use when a request will produce a specification. Never overwrites.",
     "inputSchema": {"type": "object", "required": ["title"], "properties": {
         "title": {"type": "string"}, "slug": {"type": "string", "description": "folder name; derived from title"},
         "summary": {"type": "string", "description": "one neutral paragraph: the goal"}}}},
    {"name": "kanban_move",
     "description": "Move a ticket to an ADLC stage (discovery, architect, approval, developer, qa, demo, e2e, done): "
                    "sets `status:` in its README.md and ticks the Progress boxes of earlier stages.",
     "inputSchema": {"type": "object", "required": ["ticket", "stage"], "properties": {
         "ticket": {"type": "string", "description": "slug (folder name)"}, "stage": STAGE}}},
    {"name": "kanban_add_subtask",
     "description": "Add a sub-task file .SDD/specs/<ticket>/tasks/NN-<slug>.md (status: todo) with optional checklist "
                    "items. PRDs are written by the sdd Architect stage as prd/PRD-NN-*.md; don't duplicate them here.",
     "inputSchema": {"type": "object", "required": ["ticket", "title"], "properties": {
         "ticket": {"type": "string"}, "title": {"type": "string"},
         "status": {"type": "string", "enum": list(km.SUB_STATUSES)},
         "checklist": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "kanban_set_status",
     "description": "Set `status:` of a sub-task file (prd/*.md or tasks/*.md: todo, doing, blocked, done).",
     "inputSchema": {"type": "object", "required": ["path", "status"], "properties": {
         "path": {"type": "string", "description": "path relative to the project, e.g. .SDD/specs/x/prd/PRD-01-a.md"},
         "status": {"type": "string", "enum": list(km.SUB_STATUSES)}}}},
    {"name": "kanban_check",
     "description": "Tick (or untick) a markdown checkbox in a ticket file, by 1-based number or by the start of its "
                    "label.",
     "inputSchema": {"type": "object", "required": ["path", "item"], "properties": {
         "path": {"type": "string"}, "item": {"type": ["integer", "string"]},
         "done": {"type": "boolean", "default": True}}}},
]

UI_URL = None


# ---------------------------------------------------------------- tools
def call_tool(name: str, a: dict) -> str:
    if name == "kanban_board":
        return f"{km.render_board(ROOT)}\n\nBoard: {UI_URL or 'web board not running'}"
    if name == "kanban_new_ticket":
        t = km.create_ticket(ROOT, a["title"], a.get("slug"), a.get("summary", ""))
        return f"Created ticket {t['id']} in Discovery: {t['path']}"
    if name == "kanban_move":
        t = km.move_ticket(ROOT, a["ticket"], a["stage"])
        return f"Moved {t['id']} to {km.STAGE_LABELS[t['status']]} ({t['path']})"
    if name == "kanban_add_subtask":
        rel = km.add_subtask(ROOT, a["ticket"], a["title"], a.get("status", "todo"), a.get("checklist"))
        return f"Added sub-task {rel}"
    if name == "kanban_set_status":
        return km.set_status(ROOT, a["path"], a["status"])
    if name == "kanban_check":
        return km.check(ROOT, a["path"], a["item"], a.get("done", True))
    raise ValueError(f"unknown tool {name}")


# ---------------------------------------------------------------- MCP (stdio)
def respond(msg_id, result=None, error=None) -> None:
    payload = {"jsonrpc": "2.0", "id": msg_id}
    payload.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method, msg_id = msg.get("method"), msg.get("id")
    if msg_id is None:  # notifications need no reply
        return
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        respond(msg_id, {"protocolVersion": asked if asked in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
                         "capabilities": {"tools": {}}, "serverInfo": {"name": "kanban", "version": VERSION}})
    elif method == "ping":
        respond(msg_id, {})
    elif method == "tools/list":
        respond(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        try:
            text = call_tool(params.get("name", ""), params.get("arguments") or {})
            respond(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except (KeyError, ValueError, OSError) as exc:
            respond(msg_id, {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True})
    else:
        respond(msg_id, error={"code": -32601, "message": f"Method not found: {method}"})


def serve_stdio() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            respond(None, error={"code": -32700, "message": "Parse error"})
            continue
        try:
            handle(msg)
        except Exception as exc:
            respond(msg.get("id"), error={"code": -32603, "message": str(exc)})


# ---------------------------------------------------------------- markdown view (small, safe subset)
def _inline(text: str, base: Path) -> str:
    out = html.escape(text, quote=True)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)

    def link(m):
        label, target = m.group(1), html.unescape(m.group(2))
        if re.match(r"^https?://", target):
            return f'<a href="{html.escape(target)}" rel="noopener noreferrer" target="_blank">{label}</a>'
        path_part = target.split("#", 1)[0]
        if path_part.endswith(".md"):
            rel = os.path.relpath((base / path_part).resolve(), ROOT)
            return f'<a href="/view?path={urllib.parse.quote(rel)}">{label}</a>'
        return label
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, out)


def render_md(path: Path) -> str:
    text = path.read_text()
    fields, _, body = km.split_frontmatter(text)
    base, parts, fenced, in_list, table = path.parent, [], False, False, []

    def flush_table():
        if not table:
            return
        rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in table]
        rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]  # drop |---| separators
        head, *body_rows = rows
        parts.append("<table><thead><tr>" + "".join(f"<th>{_inline(c, base)}</th>" for c in head) + "</tr></thead><tbody>"
                     + "".join("<tr>" + "".join(f"<td>{_inline(c, base)}</td>" for c in r) + "</tr>" for r in body_rows)
                     + "</tbody></table>")
        table.clear()
    if fields:
        parts.append("<p class=fm>" + " · ".join(f"<b>{html.escape(k)}</b>: {html.escape(v)}"
                                                  for k, v in fields.items()) + "</p>")
    for line in body.splitlines():
        if km.FENCE_RE.match(line):
            parts.append("</pre>" if fenced else "<pre>")
            fenced = not fenced
            continue
        if fenced:
            parts.append(html.escape(line))
            continue
        if line.lstrip().startswith("|"):
            table.append(line)
            continue
        flush_table()
        item = re.match(r"^(\s*)[-*] (.*)$", line)
        if item and not in_list:
            parts.append("<ul>")
            in_list = True
        if not item and in_list:
            parts.append("</ul>")
            in_list = False
        if item:
            box = km.CHECK_RE.match(line)
            content = (f'<input type=checkbox disabled {"checked" if box.group(2) != " " else ""}> '
                       + _inline(box.group(4), base)) if box else _inline(item.group(2), base)
            parts.append(f"<li>{content}</li>")
        elif m := re.match(r"^(#{1,6}) (.*)$", line):
            n = len(m.group(1))
            parts.append(f"<h{n}>{_inline(m.group(2), base)}</h{n}>")
        elif line.strip():
            parts.append(f"<p>{_inline(line, base)}</p>")
    flush_table()
    if in_list:
        parts.append("</ul>")
    if fenced:
        parts.append("</pre>")
    rel = path.relative_to(ROOT).as_posix()
    editor = html.escape(EDITOR_URL.format(path=str(path)))
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(path.name)}</title>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:16px;color:#1d2330}"
            "pre{background:#f4f5f7;padding:8px;overflow:auto}table{border-collapse:collapse;margin:8px 0;display:block;overflow-x:auto}"
            "th,td{border:1px solid #d0d5dd;padding:4px 8px;text-align:left;vertical-align:top}th{background:#f4f5f7}"
            "code{background:#f4f5f7;padding:0 3px}.fm{color:#667085;font-size:13px}nav{font-size:13px;margin-bottom:8px}"
            "@media(prefers-color-scheme:dark){body{background:#12151b;color:#e6e9ef}pre,code{background:#232a35}}</style>"
            f"<nav><a href='/'>← Board</a> · <code>{html.escape(rel)}</code> · <a href='{editor}'>Open in editor</a></nav>"
            + "\n".join(parts))


# ---------------------------------------------------------------- web board
class BoardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # stdout belongs to MCP
        pass

    def _host_ok(self) -> bool:  # DNS-rebinding guard: loopback host names only
        return (self.headers.get("Host") or "").rsplit(":", 1)[0] in ("127.0.0.1", "localhost")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/board":
            body = {"project": ROOT.name, "root": str(ROOT), "editor": EDITOR_URL,
                    "stages": [[s, km.STAGE_LABELS[s]] for s in km.STAGES], "tickets": km.list_tickets(ROOT)}
            return self._send(200, json.dumps(body).encode(), "application/json")
        if url.path == "/view":
            try:
                path = km.safe_path(ROOT, urllib.parse.parse_qs(url.query).get("path", [""])[0])
                if path.suffix != ".md" or not path.is_file():
                    raise ValueError("not a markdown file")
            except ValueError as exc:
                return self._send(404, str(exc).encode(), "text/plain")
            return self._send(200, render_md(path).encode(), "text/html; charset=utf-8")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Kanban") != "1":  # no cross-site posts
            return self._send(403, b"forbidden", "text/plain")
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/move":
                km.move_ticket(ROOT, req["ticket"], req["stage"])
            elif self.path == "/api/status":
                km.set_status(ROOT, req["path"], req["status"])
            elif self.path == "/api/new":
                km.create_ticket(ROOT, req["title"])
            else:
                return self._send(404, b"not found", "text/plain")
            self._send(200, b'{"ok":true}', "application/json")
        except (KeyError, ValueError, OSError) as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json")


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def _url_file() -> Path:
    folder = ROOT / ".kanban"
    folder.mkdir(exist_ok=True)
    if not (folder / ".gitignore").exists():
        (folder / ".gitignore").write_text("*\n")
    return folder / "url"


def _existing_ui() -> str | None:
    path = _url_file()
    if not path.exists():
        return None
    import urllib.request
    url = path.read_text().strip()
    try:
        with urllib.request.urlopen(url + "api/board", timeout=1) as resp:
            return url if json.load(resp).get("root") == str(ROOT) else None
    except Exception:
        return None


def start_ui(block: bool = False) -> str | None:
    global UI_URL
    UI_URL = _existing_ui()
    if UI_URL and not block:
        return UI_URL
    base = int(os.environ.get("KANBAN_PORT") or 8700 + int(hashlib.sha1(str(ROOT).encode()).hexdigest(), 16) % 200)
    for port in range(base, base + 20):
        try:
            httpd = _Server(("127.0.0.1", port), BoardHandler)
        except OSError:
            continue
        UI_URL = f"http://127.0.0.1:{port}/"
        _url_file().write_text(UI_URL + "\n")
        if block:
            print(f"Kanban board for {ROOT}: {UI_URL}", file=sys.stderr)
            httpd.serve_forever()
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return UI_URL
    return None


if __name__ == "__main__":
    if "--ui" in sys.argv:
        start_ui(block=True)
    else:
        if os.environ.get("KANBAN_NO_UI") != "1":
            try:
                start_ui()
            except Exception as exc:
                print(f"kanban: web board not started: {exc}", file=sys.stderr)
        serve_stdio()
