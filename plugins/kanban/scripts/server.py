#!/usr/bin/env python3
"""Kanban MCP server (stdio, JSON-RPC 2.0, newline-delimited) + a tiny local web board.

Claude Code starts this through the plugin's .mcp.json. It exposes five tools and, in a background
thread, serves the board at http://127.0.0.1:<port>/ (port chosen per project; written to .kanban/url).
Run `server.py --ui` to serve only the web board without Claude. Standard library only.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kanban_store as ks  # noqa: E402

VERSION = "0.1.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
ROOT = ks.project_dir()
UI_FILE = Path(__file__).with_name("ui.html")
STATUS_ENUM = {"type": "string", "enum": list(ks.STATUSES)}

TOOLS = [
    {"name": "kanban_list",
     "description": "Show the kanban board (optionally one column) and its web URL. Card ids look like K-12.",
     "inputSchema": {"type": "object", "properties": {"status": STATUS_ENUM}}},
    {"name": "kanban_add",
     "description": "Add a card, e.g. an SDD stage, a PRD or a sub-task. Use parent_id to nest it under a feature card.",
     "inputSchema": {"type": "object", "required": ["title"], "properties": {
         "title": {"type": "string"}, "status": STATUS_ENUM, "parent_id": {"type": "string"},
         "note": {"type": "string"}}}},
    {"name": "kanban_move",
     "description": "Move a card to another column: todo, doing (In progress), review or done.",
     "inputSchema": {"type": "object", "required": ["card_id", "status"], "properties": {
         "card_id": {"type": "string"}, "status": STATUS_ENUM}}},
    {"name": "kanban_update",
     "description": "Rename a card, set its note, add a step (checklist item) or tick a step (1-based).",
     "inputSchema": {"type": "object", "required": ["card_id"], "properties": {
         "card_id": {"type": "string"}, "title": {"type": "string"}, "note": {"type": "string"},
         "add_step": {"type": "string"}, "complete_step": {"type": "integer", "minimum": 1}}}},
    {"name": "kanban_delete",
     "description": "Delete a card (and its child cards). Use only for mistakes or duplicates.",
     "inputSchema": {"type": "object", "required": ["card_id"], "properties": {"card_id": {"type": "string"}}}},
]

UI_URL = None  # set once the web board is listening


# ---------------------------------------------------------------- tools
def call_tool(name: str, args: dict) -> str:
    with ks.board(ROOT, write=name != "kanban_list") as data:
        if name == "kanban_list":
            text = ks.render(data, args.get("status"))
            return f"{text}\n\nBoard: {UI_URL or 'web board not running'}"
        if name == "kanban_add":
            card = ks.add_card(data, args["title"], args.get("status", "todo"), "claude",
                               args.get("parent_id"), args.get("note", ""))
            return f"Added {card['id']} to {ks.STATUS_LABELS[card['status']]}: {card['title']}"
        if name == "kanban_move":
            card = ks.move_card(data, args["card_id"], args["status"])
            return f"Moved {card['id']} to {ks.STATUS_LABELS[card['status']]}"
        if name == "kanban_update":
            card = ks.update_card(data, args["card_id"], args.get("title"), args.get("note"),
                                  args.get("add_step"), args.get("complete_step"))
            done = sum(s["done"] for s in card["steps"])
            return f"Updated {card['id']} ({done}/{len(card['steps'])} steps done)"
        if name == "kanban_delete":
            ks.delete_card(data, args["card_id"])
            return f"Deleted {args['card_id']}"
    raise ValueError(f"unknown tool {name}")


# ---------------------------------------------------------------- MCP (stdio)
def respond(msg_id, result=None, error=None) -> None:
    payload = {"jsonrpc": "2.0", "id": msg_id}
    payload.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method, msg_id = msg.get("method"), msg.get("id")
    if msg_id is None:  # notification (e.g. notifications/initialized): no reply
        return
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        respond(msg_id, {"protocolVersion": asked if asked in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "kanban", "version": VERSION}})
    elif method == "ping":
        respond(msg_id, {})
    elif method == "tools/list":
        respond(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        try:
            text = call_tool(params.get("name", ""), params.get("arguments") or {})
            respond(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except (KeyError, ValueError) as exc:
            respond(msg_id, {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True})
    else:
        respond(msg_id, error={"code": -32601, "message": f"Method not found: {method}"})


def serve_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            respond(None, error={"code": -32700, "message": "Parse error"})
            continue
        try:
            handle(msg)
        except Exception as exc:  # keep the server alive
            respond(msg.get("id"), error={"code": -32603, "message": str(exc)})


# ---------------------------------------------------------------- web board
class BoardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # stdout belongs to MCP; stay quiet
        pass

    def _host_ok(self) -> bool:  # blocks DNS-rebinding: only loopback host names
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost")

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
        if self.path in ("/", "/index.html"):
            return self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/board":
            with ks.board(ROOT, write=False) as data:
                body = {"project": ROOT.name, "statuses": ks.STATUS_LABELS, "cards": data["cards"]}
            return self._send(200, json.dumps(body).encode(), "application/json")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        # Custom header = no cross-site form posts (a browser needs a CORS preflight we never grant).
        if not self._host_ok() or self.headers.get("X-Kanban") != "1":
            return self._send(403, b"forbidden", "text/plain")
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            with ks.board(ROOT) as data:
                if self.path == "/api/move":
                    ks.move_card(data, req["card_id"], req["status"])
                elif self.path == "/api/delete":
                    ks.delete_card(data, req["card_id"])
                elif self.path == "/api/add":
                    ks.add_card(data, req["title"], req.get("status", "todo"), "user")
                else:
                    return self._send(404, b"not found", "text/plain")
            self._send(200, b'{"ok":true}', "application/json")
        except (KeyError, ValueError) as exc:
            self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json")


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def _existing_ui() -> str | None:
    """If another session already serves this project's board, reuse its URL."""
    url_file = ks.board_dir(ROOT) / "url"
    if not url_file.exists():
        return None
    import urllib.request
    url = url_file.read_text().strip()
    try:
        with urllib.request.urlopen(url + "api/board", timeout=1) as resp:
            return url if json.load(resp).get("project") == ROOT.name else None
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
        (ks.board_dir(ROOT) / "url").write_text(UI_URL + "\n")
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
