"""Sample daemon plug-in (architecture §3.1) used by tests/test_daemon.py through KANBAN_ROUTES_DIRS."""

STATE = {"started": False, "moves": [], "guarded": False}


def register(ctx):
    def ping(req):
        return {"pong": True, "started": STATE["started"], "guarded": STATE["guarded"],
                "setting": ctx.settings.get("sample.greeting", "none")}

    def moves(req):
        return {"moves": [m for m in STATE["moves"] if m[0] == req.params["project"]]}

    def live(req):
        project = req.params["project"]
        return {"live": [s["id"] for s in ctx.live_sessions(project)],
                "channel": [s["id"] for s in ctx.live_sessions(project, channel=True)]}

    def publish(req):
        root = ctx.project(req.params["project"])  # registry lookup only
        ctx.publish(req.params["project"], "sample.event", {"folder": root.name})
        return {"ok": True}

    def on_move(project, ticket, src, dst, handoff):
        STATE["moves"].append([project, ticket, src, dst, handoff and handoff["status"]])

    def on_start(context):
        context.settings.set("sample.greeting", "hello")
        STATE["started"] = True

    ctx.route("GET", "/api/sample/ping", ping, "read")
    ctx.route("GET", "/api/projects/{project}/sample/moves", moves, "read")
    ctx.route("GET", "/api/projects/{project}/sample/live", live, "read")
    ctx.route("POST", "/api/projects/{project}/sample/publish", publish, "client")
    ctx.on_human_move(on_move)
    ctx.on_startup(on_start)
    try:  # a human-only endpoint cannot be registered with a weaker scope
        ctx.route("PUT", "/api/projects/{project}/files/{path}", ping, "client")
    except ValueError:
        STATE["guarded"] = True
