"""A fake GitHub release for tests/test_install.py: a tiny Kanban.app, its zip and SHA256SUMS, and a loopback server.

Nothing here touches the network: the server binds 127.0.0.1 only, and install.py is pointed at it through the
loopback-only AGENTIC_KIT_RELEASES_URL override. Routes mirror the installer's URL layout under that override:

  <base>/download/<tag>/SHA256SUMS
  <base>/download/<tag>/Kanban.app.zip
  <base>/api/releases?per_page=100          (stands in for api.github.com/repos/<owner>/<repo>/releases)
"""
from __future__ import annotations

import hashlib
import http.server
import json
import plistlib
import threading
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE_ID = "dev.agentic-kit.kanban"


def make_app(parent: Path, version: str = "0.3.1", bundle_id: str = BUNDLE_ID, name: str = "Kanban.app") -> Path:
    """Write a minimal app bundle: Contents/Info.plist + Contents/MacOS/kanban (a text stub, never executed)."""
    app = parent / name
    (app / "Contents/MacOS").mkdir(parents=True, exist_ok=True)
    info = {"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version, "CFBundleName": "Kanban",
            "CFBundleExecutable": "kanban"}
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    (app / "Contents/MacOS/kanban").write_text("#!/bin/sh\n# fake Kanban executable for installer tests\n")
    return app


def make_zip(out: Path, version: str = "0.3.1", bundle_id: str = BUNDLE_ID, extra_top_level: str | None = None,
             top_name: str = "Kanban.app") -> bytes:
    """Build Kanban.app.zip (top level `Kanban.app/`, like `ditto -c -k --keepParent`) and return its bytes."""
    src = out.parent / f".src-{out.name}"
    app = make_app(src, version, bundle_id, top_name)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(app.rglob("*")):
            zf.write(path, path.relative_to(src).as_posix())
        if extra_top_level:
            zf.writestr(extra_top_level, "unexpected\n")
    return out.read_bytes()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sums_text(assets: dict[str, bytes]) -> str:
    """SHA256SUMS in `shasum -a 256` format."""
    return "".join(f"{sha256(data)}  {name}\n" for name, data in sorted(assets.items()))


def releases_api() -> list[dict]:
    """The fixture releases listing: published, draft and prerelease entries (see releases.json)."""
    return json.loads((HERE / "releases.json").read_text())


class FakeRelease:
    """Loopback HTTP server. `routes` maps a path (with query) to (status, headers, body bytes); every request's path
    is appended to `log`. Unknown paths answer 404."""

    def __init__(self):
        self.routes: dict[str, tuple[int, dict, bytes]] = {}
        self.log: list[str] = []
        self.user_agents: list[str] = []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                fake.log.append(self.path)
                fake.user_agents.append(self.headers.get("User-Agent", ""))
                status, headers, body = fake.routes.get(self.path, (404, {}, b"not found\n"))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # keep test output quiet
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "FakeRelease":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # ------------------------------------------------------------------ route helpers
    def ok(self, path: str, body: bytes | str, content_type: str = "application/octet-stream") -> None:
        self.routes[path] = (200, {"Content-Type": content_type}, body.encode() if isinstance(body, str) else body)

    def status(self, path: str, code: int) -> None:
        self.routes[path] = (code, {}, b"")

    def redirect(self, path: str, location: str, code: int = 302) -> None:
        self.routes[path] = (code, {"Location": location}, b"")

    def release(self, tag: str, zip_bytes: bytes, sums: str | None = None) -> None:
        """Publish `tag` with Kanban.app.zip and SHA256SUMS (computed from the zip unless given)."""
        self.ok(f"/download/{tag}/Kanban.app.zip", zip_bytes)
        self.ok(f"/download/{tag}/SHA256SUMS", sums if sums is not None else sums_text({"Kanban.app.zip": zip_bytes}),
                "text/plain")

    def api(self, releases: list[dict]) -> None:
        self.ok("/api/releases?per_page=100", json.dumps(releases), "application/json")

    def asset_requests(self) -> list[str]:
        return [p for p in self.log if p.endswith("/Kanban.app.zip")]
