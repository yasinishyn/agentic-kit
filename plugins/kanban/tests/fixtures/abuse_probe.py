"""Abuse checklist probes (sdd abuse-checklist AB-01..AB-11, harness A: raw HTTP) against a throwaway kanban daemon.
Run by tests/test_abuse.py; temporary KANBAN_HOME and projects only; exits 1 if any probe fails."""
import hashlib, http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
home, proj, other = (Path(tempfile.mkdtemp(prefix=p)) for p in ("kb-home-", "kb-proj-", "kb-other-"))
for p in (proj, other):
    (p / ".git").mkdir()
sys.path.insert(0, str(PLUGIN / "scripts"))
os.environ["KANBAN_HOME"] = str(home)
import kanban_md as km  # noqa: E402

km.create_ticket(proj, "Demo login", slug="demo-login")
spec = proj / ".SDD/specs/demo-login"
(spec / "03-architecture.md").write_text("# Arch\n\n| A | B |\n|---|---|\n| x | y |\n\nXSS: <script>alert(1)</script> "
    "\"><img src=x onerror=alert(1)> {{7*7}} [click](javascript:alert(1)) [data](data:text/html,x) "
    "[ok](https://example.com/\"onmouseover=alert(1)) ‮evil\n")
(proj / "secret.md").write_text("outside specs\n")
os.symlink(proj / "secret.md", spec / "link.md")
km.create_ticket(other, "Other ticket", slug="other-ticket")

env = {**os.environ, "KANBAN_HOME": str(home), "KANBAN_NO_DAEMON": "1"}
d = subprocess.Popen([sys.executable, str(PLUGIN / "scripts/daemon.py"), "--foreground", "--port", "0"], env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
rows = []
def row(rid, probe, expected, ok, observed):
    rows.append((rid, probe, expected, "PASS" if ok else "FAIL", observed)); print(f"{'PASS' if ok else 'FAIL'} {rid} {probe} -> {observed}")

try:
    for _ in range(100):
        if (home / "daemon.json").exists(): break
        time.sleep(0.1)
    port = json.loads((home / "daemon.json").read_text())["port"]
    ui, cl = (home / "ui.token").read_text().strip(), (home / "client.token").read_text().strip()

    def req(method, path, body=None, token=None, headers=None, raw=None):
        h = {"Host": f"127.0.0.1:{port}"}
        if token: h["Authorization"] = f"Bearer {token}"
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None and "Content-Type" not in (headers or {}): h["Content-Type"] = "application/json"
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse(); b = r.read(); c.close()
        try: j = json.loads(b)
        except ValueError: j = None
        return r.status, j, b, dict(r.getheaders())

    s, j, *_ = req("POST", "/api/projects", {"project_root": str(proj)}, cl); pid = j["project"]["id"]
    s, j, *_ = req("POST", "/api/projects", {"project_root": str(other)}, cl); oid = j["project"]["id"]
    base = f"/api/projects/{pid}"

    # AB-03 CSRF / cross-site
    s, *_ = req("POST", f"{base}/tickets/demo-login/move", {"stage": "architect"})
    row("AB-03.1", "move without token", "401", s == 401, s)
    s, *_ = req("POST", f"{base}/tickets/demo-login/move", {"stage": "architect"}, "wrong")
    row("AB-03.2", "move with wrong token", "401", s == 401, s)
    s, *_ = req("POST", f"{base}/tickets/demo-login/move", {"stage": "architect"}, ui, {"Origin": "https://evil.test"})
    row("AB-03.3", "move with foreign Origin (valid token)", "403", s == 403, s)
    s, *_ = req("POST", f"{base}/tickets/demo-login/move", {"stage": "architect"}, ui, {"Host": "evil.test"})
    row("AB-03.4", "DNS rebinding Host header", "403", s == 403, s)
    s, _, _, hdr = req("OPTIONS", f"{base}/tickets/demo-login/move", None, None, {"Origin": "https://evil.test",
                       "Access-Control-Request-Method": "POST"})
    row("AB-03.5", "CORS preflight from foreign origin", "no Access-Control-Allow-Origin", not any(k.lower().startswith("access-control") for k in hdr), f"{s}, ACAO={hdr.get('Access-Control-Allow-Origin')}")
    s, *_ = req("GET", f"{base}/tickets/demo-login/move", None, ui)
    row("AB-03.6", "GET on a mutating route", "405, no state change", s == 405 and km.read_ticket(proj, spec)["status"] == "discovery", s)

    # AB-08 permission (scope) bypass
    for rid, m, path, body in [("AB-08.1", "POST", f"{base}/tickets/demo-login/move", {"stage": "architect"}),
                               ("AB-08.2", "POST", f"{base}/tickets/demo-login/approve", {}),
                               ("AB-08.3", "PUT", f"{base}/files?path=.SDD/specs/demo-login/README.md", {"content": "x"}),
                               ("AB-08.4", "POST", f"{base}/tickets/demo-login/runs/start", {}),
                               ("AB-08.5", "POST", "/api/runs/r-x/stop", {}),
                               ("AB-08.6", "POST", f"{base}/tickets/demo-login/move%2F", {"stage": "architect"})]:
        s, *_ = req(m, path, body, cl)
        row(rid, f"client (Claude) token on {m} {path.replace(base, '…')}", "403 (or 404 for the encoded path)", s in (403, 404) and (s == 403 or rid == "AB-08.6"), s)
    s, *_ = req("POST", "/api/projects", {"project_root": str(proj)}, ui)
    row("AB-08.7", "UI token on client-only route (register project)", "403", s == 403, s)

    # AB-01 tampered fields
    s, j, *_ = req("POST", f"{base}/tickets/demo-login/move", {"stage": "architect", "actor": "claude"}, ui)
    import sqlite3
    with sqlite3.connect(home / "kanban.db") as _db:
        hand = _db.execute("select actor from handoffs order by rowid desc limit 1").fetchone()[0]
    row("AB-01.1", "move with posted actor=claude", "actor recorded as human (board)", s == 200 and hand == "human (board)", f"{s}, actor={hand}")
    s, j, *_ = req("POST", "/api/sessions", {"project_root": str(proj), "kind": "headless", "run_id": "r-ghost", "claude_pid": os.getpid()}, cl)
    row("AB-01.2", "session registration claiming kind=headless/run_id", "server derives interactive, run_id ignored", s == 200 and j.get("kind") == "interactive", f"{s}, kind={j and j.get('kind')}")
    sid = j["session_id"]
    s, j, *_ = req("POST", f"{base}/tickets/demo-login/move", {"stage": "sideways"}, ui)
    row("AB-01.3", "off-list stage value", "400", s == 400, s)
    s, *_ = req("POST", f"{base}/tickets/demo-login/move", {"stage": "architect" * 2000}, ui)
    row("AB-01.4", "10 kB stage value", "400", s == 400, s)
    rd = (spec / "README.md").read_bytes(); sha = hashlib.sha256(rd).hexdigest()
    tampered = rd.decode().replace("status: architect", "status: done")
    s, *_ = req("PUT", f"{base}/files?path=.SDD/specs/demo-login/README.md", {"content": tampered}, ui, {"If-Match": sha})
    row("AB-01.5", "editor PUT changing status: to done", "409 protected, file unchanged", s == 409 and (spec / "README.md").read_bytes() == rd, s)
    tampered = rd.decode().replace("---\n\n", "approved_hash: " + "0" * 64 + "\n---\n\n", 1)
    s, *_ = req("PUT", f"{base}/files?path=.SDD/specs/demo-login/README.md", {"content": tampered}, ui, {"If-Match": sha})
    row("AB-01.6", "editor PUT adding approved_hash", "409 protected", s == 409, s)

    # AB-02 step skipping (approval gate)
    s, j, *_ = req("POST", f"{base}/tickets/demo-login/move", {"stage": "done"}, ui)
    row("AB-02.1", "architect → done without approval", "409 approval_required", s == 409 and (j or {}).get("reason") == "approval_required", f"{s} {(j or {}).get('reason')}")
    s, j, *_ = req("POST", f"{base}/tickets/demo-login/move", {"stage": "developer"}, ui)
    row("AB-02.2", "architect → developer without approval", "409", s == 409, s)
    s, *_ = req("POST", f"/api/projects/{pid}/tickets/demo-login/approve-chat", {}, cl, {"X-Kanban-Session": "nope"})
    row("AB-02.3", "approve-chat with unknown session", "403", s == 403, s)

    # AB-04 cross-project (IDOR-like)
    s, *_ = req("POST", f"/api/projects/{oid}/tickets/other-ticket/approve-chat", {}, cl, {"X-Kanban-Session": sid})
    row("AB-04.1", "approve-chat on another project's ticket with this project's session", "403", s == 403, s)
    s, *_ = req("GET", f"/api/projects/{pid}/files?path=" + str(other / ".SDD/specs/other-ticket/README.md"), None, ui)
    row("AB-04.2", "read another project's file by absolute path through this project", "4xx", 400 <= s < 500, s)
    s, *_ = req("GET", "/api/projects/nope/board", None, ui)
    row("AB-04.3", "unknown project id", "404", s == 404, s)

    # AB-06 local file read
    for rid, path in [("AB-06.1", "../../secret.md"), ("AB-06.2", ".SDD/specs/demo-login/link.md"), ("AB-06.3", "/etc/hosts"),
                      ("AB-06.4", ".SDD/specs/../secret.md"), ("AB-06.5", "%2e%2e/%2e%2e/secret.md")]:
        s1, *_ = req("GET", f"{base}/files?path={path}", None, ui)
        s2, *_ = req("GET", f"{base}/view?path={path}", None, ui)
        row(rid, f"read {path} via /files and /view", "4xx both", 400 <= s1 < 500 and 400 <= s2 < 500, f"{s1}/{s2}")
    s, *_ = req("GET", "/ui/..%2f..%2fscripts/daemon.py")
    row("AB-06.6", "static path traversal to daemon.py", "404", s == 404, s)

    # AB-05 XSS in rendered markdown
    s, j, *_ = req("GET", f"{base}/view?path=.SDD/specs/demo-login/03-architecture.md", None, ui)
    h = (j or {}).get("html", "")
    import re as _re
    bad = _re.findall(r"<(?:script|img|svg|iframe)\b|<a [^>]*href=\"(?!https?://|#)[^\"]*\"|<[^>]* on[a-z]+=", h)
    row("AB-05.1", "markdown payloads rendered by /view", "escaped; no script/onerror/javascript:/data: href", s == 200 and not bad and "&lt;script&gt;" in h, f"{s}, raw hits={bad}")
    row("AB-05.2", "template syntax {{7*7}}", "literal, 49 absent", "49" not in h and "{{7*7}}" in h, "49" in h)

    # AB-07 upload-like body limits
    try:
        s, *_ = req("PUT", f"{base}/files?path=.SDD/specs/demo-login/README.md", None, ui, {"If-Match": sha}, raw=b"x" * (4 * 1024 * 1024 + 1))
    except (BrokenPipeError, ConnectionResetError) as exc:
        s = f"connection closed before the body was read ({type(exc).__name__})"
    row("AB-07.1", "request body over 4 MB", "413 or refused before reading, file unchanged", (s == 413 or "closed" in str(s)) and (spec / "README.md").read_bytes() == rd, s)

    # AB-10 secrets exposure
    leaks = []
    for path in ("/api/health", "/api/projects", f"{base}/board", "/api/runs"):
        _, _, b, _ = req("GET", path, None, ui)
        if ui.encode() in b or cl.encode() in b: leaks.append(path)
    files = [p for p in home.iterdir() if p.is_file() and p.name not in ("ui.token", "client.token")]
    leaks += [p.name for p in files if ui.encode() in p.read_bytes() or cl.encode() in p.read_bytes()]
    row("AB-10.1", "tokens in API responses, daemon.json, log, db", "none", not leaks, leaks or "none")
    perms = {p.name: oct(p.stat().st_mode & 0o777) for p in home.iterdir() if p.is_file()}
    row("AB-10.2", "KANBAN_HOME permissions", "dir 0700, files 0600", oct(home.stat().st_mode & 0o777) == "0o700" and all(v == "0o600" for v in perms.values()), perms)

    # AB-11 headers
    s, _, b, hdr = req("GET", "/")
    need = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}
    row("AB-11.1", "headers on /", "no-store, nosniff, DENY, CSP script-src 'self'", all(hdr.get(k) == v for k, v in need.items()) and "script-src 'self';" in hdr.get("Content-Security-Policy", ""), {k: hdr.get(k) for k in need})
    s, _, b, hdr = req("GET", f"{base}/board", None, ui)
    row("AB-11.2", "JSON content type on API", "application/json", hdr.get("Content-Type") == "application/json", hdr.get("Content-Type"))
    s, _, b, _ = req("POST", f"{base}/tickets/demo-login/move", None, ui, raw=b"{not json")
    row("AB-11.3", "malformed JSON body", "400, no stack trace", s == 400 and b"Traceback" not in b, s)
finally:
    d.terminate(); d.wait(timeout=10)
    import shutil
    for p in (home, proj, other): shutil.rmtree(p, ignore_errors=True)
fails = [r for r in rows if r[3] == "FAIL"]
print(f"abuse probes: {len(rows) - len(fails)}/{len(rows)} passed")
sys.exit(1 if fails else 0)
