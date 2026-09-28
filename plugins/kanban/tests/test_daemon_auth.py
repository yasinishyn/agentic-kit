#!/usr/bin/env python3
"""Local API security of the board daemon (architecture §8, ADR-007): scoped tokens, Host/Origin checks, no CORS,
exact CSP, static files without data, no inline script in ui/, rejected bodies drained (stdlib only)."""
from __future__ import annotations

import re
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

import helpers
import daemon as kd
import kanban_md as km

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self' ipc: http://ipc.localhost; frame-ancestors 'none'")


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kanban-auth-"))
        cls.root = helpers.make_project(cls.tmp)
        km.create_ticket(cls.root, "Login")
        cls.d = helpers.TestDaemon().start()
        cls.session = cls.d.register(cls.root)
        cls.pid = cls.session["project_id"]

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def raw(self, method, path, body=None, token=None, headers=None):
        return helpers.request(self.d.port, method, path, body, self.d.token(token), headers)

    def test_auth_scope_matrix(self):
        p = f"/api/projects/{self.pid}"
        session = {"X-Kanban-Session": self.session["session_id"]}
        cases = [  # (method, path, token, expected status)
            ("GET", "/api/health", None, 401),
            ("GET", "/api/health", "wrong-token", 401),
            ("GET", "/api/health", "client", 200),
            ("GET", "/api/health", "ui", 200),
            ("GET", "/api/projects", None, 401),
            ("GET", f"{p}/board", None, 401),
            ("GET", f"{p}/board", "client", 200),
            ("GET", f"{p}/board", "ui", 200),
            ("GET", "/api/nothing-here", None, 401),  # 401 before 404: no probing without a token
            ("GET", "/api/nothing-here", "client", 404),
            ("POST", f"{p}/tickets/login/approve", "client", 403),
            ("POST", f"{p}/tickets/login/move", "client", 403),
            ("PUT", f"{p}/files/.SDD/specs/login/README.md", "client", 403),
            ("POST", f"{p}/tickets/login/runs/start", "client", 403),
            ("POST", "/api/runs/r-1/stop", "client", 403),
            ("POST", "/api/sessions", "ui", 403),
            ("POST", "/api/projects", "ui", 403),
            ("POST", "/api/shutdown", "ui", 403),
            ("POST", f"{p}/tickets/login/approve-chat", "ui", 403),
            ("POST", f"{p}/tickets/login/move", "ui", 200),
        ]
        for method, path, token, expected in cases:
            body = {"stage": "architect"} if path.endswith("/move") else {}
            status, headers, raw = self.raw(method, path, body if method != "GET" else None, token)
            self.assertEqual(status, expected, f"{method} {path} with {token}: {raw[:200]}")
            self.assertFalse([h for h in headers if h.startswith("access-control-")], "no CORS headers")
            if status == 401:
                self.assertEqual(headers.get("www-authenticate"), "Bearer")
        status, _, raw = self.raw("POST", f"{p}/tickets/login/approve-chat", {}, "client", session)
        self.assertEqual(status, 200, raw)

    def test_host_and_origin(self):
        for headers in ({"Host": "evil.com"}, {"Host": "evil.com:80"},
                        {"Origin": "http://evil.com"}, {"Origin": "http://127.0.0.1:1"}):
            for path in ("/", "/api/health"):
                status, hdrs, _ = self.raw("GET", path, token="client", headers=headers)
                self.assertEqual(status, 403, (headers, path))
                self.assertNotIn("access-control-allow-origin", hdrs)
        status, _, _ = self.raw("GET", "/api/health", token="client",
                                headers={"Host": f"localhost:{self.d.port}",
                                         "Origin": f"http://localhost:{self.d.port}"})
        self.assertEqual(status, 200)

    def test_csp_header(self):
        for path, token in (("/", None), ("/ui/app.js", None), ("/api/health", "client"), ("/api/health", None)):
            status, headers, _ = self.raw("GET", path, token=token)
            self.assertEqual(headers["content-security-policy"], CSP, path)
            self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(kd.CSP, CSP)

    def test_static_is_unauthenticated_and_carries_no_data(self):
        status, headers, body = self.raw("GET", "/")
        self.assertEqual((status, headers["content-type"]), (200, "text/html; charset=utf-8"))
        self.assertNotIn(self.d.ui_token.encode(), body)
        self.assertNotIn(str(self.root).encode(), body)
        for path, ctype in (("/ui/app.js", "text/javascript; charset=utf-8"), ("/ui/board.js", None),
                            ("/ui/board.css", "text/css; charset=utf-8")):
            status, headers, _ = self.raw("GET", path)
            self.assertEqual(status, 200, path)
            if ctype:
                self.assertEqual(headers["content-type"], ctype)
        status, _, body = self.raw("GET", "/ui/modules")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(__import__("json").loads(body)), ["css", "js"])
        for path in ("/ui/../scripts/daemon.py", "/ui/%2e%2e/scripts/daemon.py", "/ui/modules/.gitkeep",
                     "/ui/missing.js", "/secret"):
            self.assertEqual(self.raw("GET", path)[0], 404, path)
        self.assertEqual(self.raw("POST", "/", {})[0], 405)

    def test_rejected_requests_drain_the_body(self):
        body = b"x" * 256 * 1024
        for i in range(30):  # would intermittently raise ConnectionResetError without draining
            status, _, _ = helpers.request(self.d.port, "POST", "/api/sessions", body,
                                           headers={"Content-Type": "application/json"})
            self.assertEqual(status, 401, i)
            status, _, _ = helpers.request(self.d.port, "POST", "/api/sessions", body, token=self.d.ui_token,
                                           headers={"Content-Type": "application/json"})
            self.assertEqual(status, 403, i)

    def test_oversized_body_refused(self):
        with socket.create_connection(("127.0.0.1", self.d.port), timeout=5) as sock:
            sock.sendall(f"POST /api/sessions HTTP/1.1\r\nHost: 127.0.0.1:{self.d.port}\r\n"
                         f"Content-Length: {kd.MAX_BODY + 1}\r\n\r\n".encode())
            self.assertIn(b" 413 ", sock.recv(200))


class UiSourceTests(unittest.TestCase):
    def test_no_inline_script_or_event_handler_attributes(self):
        ui = helpers.PLUGIN / "ui"
        files = [p for p in ui.rglob("*") if p.suffix in (".html", ".js", ".css")]
        self.assertTrue({"index.html", "app.js", "board.js", "board.css"} <= {p.name for p in files})
        for path in files:
            text = path.read_text()
            self.assertIsNone(re.search(r"<[a-zA-Z][^>]*\son[a-z]+\s*=", text), f"on*= attribute in {path.name}")
            if path.suffix == ".html":
                for tag in re.findall(r"<script\b[^>]*>", text):
                    self.assertIn("src=", tag, f"inline script in {path.name}")
                self.assertNotIn("javascript:", text)
            if path.suffix == ".js":
                self.assertNotIn("EventSource", text)  # the token rides in a header, never a URL
                self.assertNotIn("eval(", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
