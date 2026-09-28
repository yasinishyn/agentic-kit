"""Daemon plug-in: hand-off claims, runs, liveness, the pickup timer (PRD-03) and the headless runner (PRD-04).

Routes (architecture §3.1 contract; scopes per §8):
    POST /api/projects/{project}/tickets/{ticket}/claim     client  kanban_start: {handoff_id?, subtask?} →
                                                                    {result: ok|already_claimed|superseded|not_found,
                                                                     run_id}
    POST /api/runs/heartbeat                                client  kanban_heartbeat: {note?, subtask?}
    POST /api/runs/finish                                   client  kanban_finish: {outcome, summary}
    POST /api/hooks/activity                                client  hook.py: {event: PostToolUse|Stop, pids}
    GET  /api/projects/{project}/tickets/{ticket}/approval  read    kanban_approval
    GET  /api/projects/{project}/runs                       read    card indicators: runs + pending hand-offs
    GET  /api/runs[?live=1]                                 read    every project's runs (app menu, PRD-07)
    GET  /api/projects/{project}/tickets/{ticket}/runs      read    the ticket's runs, newest first (transcript panel)
    POST /api/projects/{project}/tickets/{ticket}/runs/start  ui    "Run headless": {handoff_id?} → {ok, run}
                                                                    (409: live run / no pending hand-off)
    POST /api/runs/{run}/stop                               ui      Stop a headless run → {ok, run} (cancelled)
    GET  /api/runs/{run}/events[?afterSeq=N&limit=M]        read    transcript page: {run, events: [{seq, at, kind,
                                                                    text}], last_seq, more}
Run summaries carry permission_denials (count) and needs_input / needs_input_summary: a headless run that exited 0
while waiting after kanban_finish(needs_input) is succeeded but flagged (run_event `needs_input`) until a human move
of the ticket (run_event `needs_input_cleared`) or a newer run of the ticket.

The calling session comes from the X-Kanban-Session header (registered by server.py); a headless session carries its
run id (KANBAN_RUN_ID), which makes its claim idempotent. kanban_finish: done → succeeded, failed → failed,
needs_input → waiting (the run keeps the ticket; the developer answers, or the headless process exit ends it).
Hook activity is matched to interactive runs by (claude pid, process start time): PostToolUse → heartbeat
(waiting → running), Stop → waiting only for a running run (terminal states are sticky).

Background loop (on_startup): hand-offs unclaimed for KANBAN_PICKUP_SECONDS (45) publish `handoff.pickup_timeout`
with action `headless` (no live interactive session of the project at all: runner.py starts a headless run through the
same path as the button; `runner: false` when it could not) or `offer` (a live session exists, channel or not: the card
offers "Run headless" / "Copy prompt"); a requeued hand-off is decided again. Live interactive runs whose claude
process is gone become `abandoned`. Every run change publishes `run.changed`; hand-offs requeued by a finished run
publish `handoff.created` (status requeued) so channel sessions receive them again. The runner is not installed
(501) in a test-isolated daemon (KANBAN_NO_DAEMON=1) unless KANBAN_CLAUDE_BIN names the binary to run, so tests never
start the real `claude`. Standard library only.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import kanban_db as kdb
import kanban_md as km
import kanban_rules as rules
import runner as krunner

OUTCOMES = {"done": "succeeded", "failed": "failed", "needs_input": "waiting"}
HOOK_EVENTS = ("PostToolUse", "Stop")
RECENT_SECONDS = 3600.0  # finished runs stay visible on the card for an hour
MAX_TEXT = 500

# fn(ctx, handoff) -> run id, set by register() when the headless runner is installed (PRD-04).
start_headless = None
_pickup: dict = {}  # handoff id → (hand-off status when decided, "headless" | "offer")
_pickup_lock = threading.Lock()


def reconcile_pickup(pending: list) -> None:
    """Keep pickup decisions only for hand-offs still pending (claimed, superseded or done ones are pruned) and
    forget the decision of a hand-off requeued since it was made, so the timer re-evaluates it."""
    status = {h["id"]: h["status"] for h in pending}
    with _pickup_lock:
        for hid, (decided, _) in list(_pickup.items()):
            now = status.get(hid)
            if now is None or (now == "requeued" and decided != "requeued"):
                del _pickup[hid]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def _text(value, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value[:MAX_TEXT]


def runner_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return not (env.get("KANBAN_NO_DAEMON") == "1" and not env.get("KANBAN_CLAUDE_BIN"))


def register(ctx):
    global start_headless
    ApiError = ctx.ApiError
    stale_ttl = _env_float("KANBAN_STALE_SECONDS", 900.0)

    # ---------------------------------------------------------------- helpers
    def summary(conn, run: dict, names: dict | None = None) -> dict:
        now = time.time()
        sub = conn.execute("SELECT data FROM run_events WHERE run_id=? AND type='subtask' ORDER BY id DESC LIMIT 1",
                           (run["id"],)).fetchone()
        out = {k: run[k] for k in ("id", "project_id", "ticket", "stage", "kind", "status", "handoff_id",
                                   "heartbeat_at", "started_at", "ended_at", "reason", "note")}
        denials = conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id=? AND type='permission_denial'",
                               (run["id"],)).fetchone()[0]
        flag = conn.execute("SELECT type, data FROM run_events WHERE run_id=? AND type IN ('needs_input', "
                            "'needs_input_cleared') ORDER BY id DESC LIMIT 1", (run["id"],)).fetchone()
        needs = bool(flag and flag[0] == "needs_input") and not conn.execute(
            "SELECT 1 FROM runs WHERE project_id=? AND ticket=? AND created_at>? AND id<>? LIMIT 1",
            (run["project_id"], run["ticket"], run["created_at"], run["id"])).fetchone()
        out.update(view=rules.view_state(run, now, stale_ttl), stage_label=km.STAGE_LABELS.get(run["stage"], ""),
                   subtask=json.loads(sub[0]).get("path") if sub else None, permission_denials=denials,
                   created_at=run["created_at"], needs_input=needs,
                   needs_input_summary=json.loads(flag[1]).get("text", "") if needs else None)
        if names is not None:
            out["project_name"] = names.get(run["project_id"], "")
        return out

    def changed(conn, run_id: str) -> dict:
        run = kdb.get_run(conn, run_id)
        data = summary(conn, run)
        ctx.publish(run["project_id"], "run.changed", {"ticket": run["ticket"], "run": data})
        return data

    def handoff_event(h: dict) -> dict:
        return {"id": h["id"], "ticket": h["ticket"], "kind": h["kind"], "stage": h["stage"],
                "from_stage": h["from_stage"], "status": h["status"], "run_id": h["run_id"], "superseded": []}

    def transition(conn, run_id: str, status: str, reason: str | None = None) -> dict:
        """kdb.transition_run plus events: run.changed, and handoff.created for every requeued hand-off."""
        before = {r[0] for r in conn.execute("SELECT id FROM handoffs WHERE run_id=? AND status='coalesced'",
                                             (run_id,))}
        kdb.transition_run(conn, run_id, status, reason=reason)
        data = changed(conn, run_id)
        for hid in sorted(before):
            h = kdb.get_handoff(conn, hid)
            if h and h["status"] == "requeued":
                ctx.publish(h["project_id"], "handoff.created", handoff_event(h))
        return data

    def session_of(req) -> dict:
        if req.session is None:
            raise ApiError(403, "this action needs a registered session (X-Kanban-Session)")
        return req.session

    def session_runs(conn, session: dict) -> list:
        """The caller's live runs, newest first (a headless session also owns its KANBAN_RUN_ID run)."""
        rows = conn.execute(f"SELECT * FROM runs WHERE status IN {kdb._LIVE_SQL} AND (session_id=? OR id=?) "
                            "ORDER BY started_at DESC, created_at DESC",
                            (session["id"], session.get("run_id") or "")).fetchall()
        return [dict(r) for r in rows]

    def set_subtask(conn, run_id: str, subtask: str | None) -> None:
        if subtask:
            conn.execute("INSERT INTO run_events (run_id, at, type, data) VALUES (?, ?, 'subtask', ?)",
                         (run_id, time.time(), json.dumps({"path": subtask})))

    # ---------------------------------------------------------------- claim (kanban_start)
    def h_claim(req):
        session = session_of(req)
        project = req.project()
        ticket = req.ticket(project)
        if session["project_id"] != project["id"]:
            raise ApiError(403, "the session belongs to another project")
        body = req.json
        hid, subtask = _text(body.get("handoff_id"), "handoff_id"), _text(body.get("subtask"), "subtask")
        conn = ctx.db()
        kind = session["kind"]
        own = session.get("run_id") or None
        mine = [r for r in session_runs(conn, session) if r["ticket"] == ticket]
        if mine and not own:
            own = mine[0]["id"]
        if not hid:  # no hand-off named: the newest pending one, else a run for the ticket's current stage
            pending = kdb.pending_handoffs(conn, project["id"], ticket)
            hid = pending[-1]["id"] if pending else None
        if hid:
            h = kdb.get_handoff(conn, hid)
            if h is not None and (h["project_id"], h["ticket"]) != (project["id"], ticket):
                raise ApiError(400, "that hand-off belongs to another ticket")
            result, run_id = kdb.claim(conn, hid, run_id=own, session_id=session["id"], kind=kind,
                                       claude_pid=session["claude_pid"],
                                       claude_start_time=session["claude_start_time"])
        else:
            live = kdb.live_run(conn, project["id"], ticket)
            if live:
                result, run_id = ("ok", live["id"]) if own and live["id"] == own else ("already_claimed", None)
                if result == "ok" and live["status"] != "running":
                    kdb.transition_run(conn, live["id"], "running")
            else:
                stage = km.read_ticket(Path(project["root"]), km.ticket_readme(Path(project["root"]), ticket).parent)
                try:
                    run_id = kdb.create_run(conn, project["id"], ticket, stage["status"], kind, status="running",
                                            session_id=session["id"], run_id=own, claude_pid=session["claude_pid"],
                                            claude_start_time=session["claude_start_time"])["id"]
                    result = "ok"
                except kdb.LiveRunExists:
                    result, run_id = "already_claimed", None
        if result == "ok":
            set_subtask(conn, run_id, subtask)
            with _pickup_lock:
                _pickup.pop(hid, None)
            run = changed(conn, run_id)
            return {"result": "ok", "run_id": run_id, "stage": run["stage"], "ticket": ticket}
        return {"result": result, "run_id": None, "ticket": ticket}

    # ---------------------------------------------------------------- heartbeat / finish
    def h_heartbeat(req):
        session = session_of(req)
        body = req.json
        note, subtask = _text(body.get("note"), "note"), _text(body.get("subtask"), "subtask")
        conn = ctx.db()
        runs = session_runs(conn, session)
        if not runs:
            return {"ok": False, "reason": "no_run", "run_id": None}
        for run in runs:
            beat(conn, run)
            if note is not None:
                conn.execute("UPDATE runs SET note=? WHERE id=?", (note, run["id"]))
            set_subtask(conn, run["id"], subtask)
            changed(conn, run["id"])
        return {"ok": True, "run_id": runs[0]["id"]}

    def beat(conn, run: dict) -> None:
        if run["status"] == "waiting":
            kdb.transition_run(conn, run["id"], "running")
        else:
            conn.execute("UPDATE runs SET heartbeat_at=? WHERE id=?", (time.time(), run["id"]))

    def h_finish(req):
        session = session_of(req)
        body = req.json
        outcome = body.get("outcome")
        if outcome not in OUTCOMES:
            raise ApiError(400, "outcome must be one of done, needs_input, failed")
        text = _text(body.get("summary"), "summary") or ""
        conn = ctx.db()
        runs = session_runs(conn, session)
        if not runs:
            return {"ok": False, "reason": "no_run", "run_id": None, "status": None}
        run, status = runs[0], OUTCOMES[outcome]
        if status == "waiting" and run["status"] == "waiting":
            conn.execute("UPDATE runs SET reason=? WHERE id=?", (text, run["id"]))
            data = changed(conn, run["id"])
        else:
            data = transition(conn, run["id"], status, reason=text)
        conn.execute("UPDATE runs SET note=? WHERE id=?", (f"{outcome}: {text}"[:MAX_TEXT], run["id"]))
        return {"ok": True, "run_id": run["id"], "status": data["status"]}

    # ---------------------------------------------------------------- hooks
    def h_activity(req):
        body = req.json
        event = body.get("event")
        if event not in HOOK_EVENTS:
            raise ApiError(400, f"event must be one of {', '.join(HOOK_EVENTS)}")
        pairs = set()
        for item in body.get("pids") or []:
            try:
                pairs.add((int(item[0]), " ".join(str(item[1]).split())))
            except (TypeError, ValueError, IndexError):
                continue
        pids = {p for p, _ in pairs}
        conn = ctx.db()
        matched = 0
        for run in kdb.live_runs(conn):
            if run["kind"] != "interactive" or run["claude_pid"] not in pids:
                continue
            start = " ".join(str(run["claude_start_time"] or "").split())
            if start and (run["claude_pid"], start) not in pairs:
                continue  # same pid, another process (pid reuse)
            matched += 1
            if event == "Stop":
                if run["status"] == "running":
                    transition(conn, run["id"], "waiting", reason="waiting for you")
            else:
                beat(conn, run)
                changed(conn, run["id"])
        return {"matched": matched}

    # ---------------------------------------------------------------- reads
    def h_approval(req):
        project = req.project()
        ticket = req.ticket(project)
        root = Path(project["root"])
        readme = km.ticket_readme(root, ticket)
        fields = km.split_frontmatter(readme.read_text())[0]
        record = rules.approval_record(fields)
        out = {"recorded": False, "valid": False, "actor": "", "at": "", "hash12": "", "board_recorded": False}
        if record is None:
            return out
        latest = kdb.latest_approval(ctx.db(), project["id"], ticket)
        recorded = bool(latest and latest["hash"] == record["hash"])
        out.update(recorded=recorded, valid=rules.approval_valid(fields, km.spec_hash(readme.parent)),
                   actor=record["by"], at=record["at"], hash12=record["hash"][:12],
                   board_recorded=recorded and latest["actor"] == "human (board)")
        return out

    def h_project_runs(req):
        project = req.project()
        conn = ctx.db()
        since = time.time() - RECENT_SECONDS
        rows = conn.execute(f"SELECT * FROM runs WHERE project_id=? AND (status IN {kdb._LIVE_SQL} OR ended_at>?) "
                            "ORDER BY created_at", (project["id"], since)).fetchall()
        latest = {}
        for row in rows:  # one run per ticket: the live one, else the newest finished one
            run = dict(row)
            if run["ticket"] not in latest or latest[run["ticket"]]["status"] not in kdb.RUN_LIVE:
                latest[run["ticket"]] = run
        handoffs = []
        for h in kdb.pending_handoffs(conn, project["id"]):
            try:
                prompt = rules.channel_event({**h, "handoff_id": h["id"]})["content"]
            except ValueError:
                prompt = ""
            with _pickup_lock:
                decision = (_pickup.get(h["id"]) or (None, None))[1]
            handoffs.append({**handoff_event(h), "created_at": h["created_at"], "pickup": decision, "prompt": prompt})
        return {"now": time.time(), "stale_seconds": stale_ttl,
                "runs": [summary(conn, r) for r in latest.values()], "handoffs": handoffs}

    def h_runs(req):
        conn = ctx.db()
        names = {p["id"]: p["name"] for p in kdb.list_projects(conn)}
        if req.query.get("live") in ("1", "true", "yes"):
            rows = kdb.live_runs(conn)
        else:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM runs WHERE status IN {kdb._LIVE_SQL} OR ended_at>? "
                                                  "ORDER BY created_at", (time.time() - RECENT_SECONDS,))]
        return {"runs": [summary(conn, r, names) for r in rows]}

    headless = krunner.Runner(ctx, transition=lambda conn, rid, status, reason: transition(conn, rid, status, reason),
                              changed=changed) if runner_enabled() else None
    start_headless = headless.start_for_handoff if headless else None

    def need_runner():
        if headless is None:
            raise ApiError(501, "runner not installed")
        return headless

    def h_run_headless(req):
        project = req.project()
        ticket = req.ticket(project)
        hid = _text(req.json.get("handoff_id"), "handoff_id")
        try:
            run = need_runner().start(project["id"], ticket, hid)
        except krunner.Conflict as exc:
            raise ApiError(409, str(exc))
        return {"ok": run["status"] != "failed", "run": summary(ctx.db(), run)}

    def h_stop(req):
        try:
            run = need_runner().stop(req.params["run"])
        except krunner.Conflict as exc:
            raise ApiError(409, str(exc))
        except KeyError:
            raise ApiError(404, "unknown run")
        return {"ok": True, "run": summary(ctx.db(), run)}

    def h_events(req):
        conn = ctx.db()
        run = kdb.get_run(conn, req.params["run"])
        if run is None:
            raise ApiError(404, "unknown run")
        try:
            after = int(req.query.get("afterSeq") or 0)
            limit = min(1000, max(1, int(req.query.get("limit") or 500)))
        except ValueError:
            raise ApiError(400, "afterSeq and limit must be integers")
        rows = conn.execute("SELECT * FROM run_events WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
                            (run["id"], after, limit + 1)).fetchall()
        events = [krunner.event_view(r) for r in rows[:limit]]
        return {"run": summary(conn, run), "events": events, "last_seq": events[-1]["seq"] if events else after,
                "more": len(rows) > limit}

    def h_ticket_runs(req):
        project = req.project()
        ticket = req.ticket(project)
        conn = ctx.db()
        rows = conn.execute("SELECT * FROM runs WHERE project_id=? AND ticket=? ORDER BY created_at DESC LIMIT 20",
                            (project["id"], ticket)).fetchall()
        return {"runs": [summary(conn, dict(r)) for r in rows], "runner": headless is not None}

    # ---------------------------------------------------------------- background: pickup timer + abandoned runs
    def tick(context, started_at: float) -> None:
        conn = context.db()
        pickup = _env_float("KANBAN_PICKUP_SECONDS", 45.0)
        now = time.time()
        pending = {p["id"]: kdb.pending_handoffs(conn, p["id"]) for p in kdb.list_projects(conn)}
        reconcile_pickup([h for hs in pending.values() for h in hs])
        for project_id, handoffs in pending.items():
            for h in handoffs:
                with _pickup_lock:
                    if h["id"] in _pickup:
                        continue
                if now - h["updated_at"] < pickup:
                    continue
                # someone is at a session of this project (channel or not): offer; auto headless only when nobody is
                live = [s for s in context.live_sessions(project_id) if s["kind"] != "headless"]
                channel = [s for s in live if s["channel"]]
                action = "offer" if live or h["updated_at"] < started_at else "headless"
                with _pickup_lock:
                    _pickup[h["id"]] = (h["status"], action)
                runner = False
                if action == "headless" and start_headless is not None:
                    try:
                        start_headless(context, h)
                        runner = True
                    except Exception as exc:  # the card still offers the actions
                        context.log(f"headless start for {h['id']} failed: {exc!r}")
                context.publish(project_id, "handoff.pickup_timeout",
                                {"id": h["id"], "ticket": h["ticket"], "stage": h["stage"], "action": action,
                                 "runner": runner, "live_channel_sessions": len(channel),
                                 "live_sessions": len(live)})
        for run in kdb.live_runs(conn):
            if run["kind"] == "interactive" and run["claude_pid"] and not kdb.process_alive(
                    run["claude_pid"], run["claude_start_time"]):
                try:
                    transition(conn, run["id"], "abandoned", reason="the Claude session ended")
                except (KeyError, ValueError):
                    pass

    def loop(context) -> None:
        started_at = time.time()
        step = min(_env_float("KANBAN_SWEEP_SECONDS", 10.0), max(0.1, _env_float("KANBAN_PICKUP_SECONDS", 45.0) / 4))

        def run():
            while True:
                try:
                    tick(context, started_at)
                except Exception as exc:
                    context.log(f"runs loop: {exc!r}")
                time.sleep(step)
        threading.Thread(target=run, name="runs-loop", daemon=True).start()

    def clear_needs_input(project_id, ticket, src, dst, handoff):
        """A human move of the ticket clears a headless run's needs_input flag (a new run clears it implicitly)."""
        conn = ctx.db()
        for (run_id,) in conn.execute("SELECT id FROM runs WHERE project_id=? AND ticket=? AND id IN (SELECT run_id "
                                      "FROM run_events WHERE type='needs_input')", (project_id, ticket)).fetchall():
            run = kdb.get_run(conn, run_id)
            if summary(conn, run)["needs_input"]:
                conn.execute("INSERT INTO run_events (run_id, at, type, data) VALUES (?, ?, 'needs_input_cleared', ?)",
                             (run_id, time.time(), json.dumps({"kind": "needs_input_cleared",
                                                               "text": f"ticket moved to {dst}"})))
                changed(conn, run_id)

    ctx.on_human_move(clear_needs_input)
    ctx.route("POST", "/api/projects/{project}/tickets/{ticket}/claim", h_claim, "client")
    ctx.route("POST", "/api/runs/heartbeat", h_heartbeat, "client")
    ctx.route("POST", "/api/runs/finish", h_finish, "client")
    ctx.route("POST", "/api/hooks/activity", h_activity, "client")
    ctx.route("GET", "/api/projects/{project}/tickets/{ticket}/approval", h_approval, "read")
    ctx.route("GET", "/api/projects/{project}/runs", h_project_runs, "read")
    ctx.route("GET", "/api/runs", h_runs, "read")
    ctx.route("POST", "/api/projects/{project}/tickets/{ticket}/runs/start", h_run_headless, "ui")
    ctx.route("GET", "/api/projects/{project}/tickets/{ticket}/runs", h_ticket_runs, "read")
    ctx.route("POST", "/api/runs/{run}/stop", h_stop, "ui")
    ctx.route("GET", "/api/runs/{run}/events", h_events, "read")
    if headless is not None:
        ctx.on_startup(headless.on_startup)  # orphans and retention before the pickup timer can start runs
    ctx.on_startup(loop)
