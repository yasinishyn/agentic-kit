#!/usr/bin/env python3
"""Claude Code hook: every user prompt becomes a card; when Claude stops, the card moves to Review.

Usage (from hooks/hooks.json): hook.py prompt | hook.py stop   — reads the hook JSON on stdin.
Never blocks: any error is swallowed and the hook exits 0, so the kanban can't get in the way of work.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kanban_store as ks  # noqa: E402


def on_prompt(event: dict) -> str | None:
    prompt = (event.get("prompt") or event.get("user_prompt") or "").strip()
    if not prompt or os.environ.get("KANBAN_DISABLE_PROMPT_CARDS") == "1":
        return None
    session = event.get("session_id") or "unknown"
    root = ks.project_dir(event.get("cwd"))
    with ks.board(root) as data:
        card = ks.add_card(data, prompt, status="doing", source="prompt", session_id=session)
        data["sessions"][session] = card["id"]
    return (f"Kanban: this prompt is card {card['id']} (In progress). Use the kanban MCP tools to add steps "
            f"or child cards for real work items; it moves to Review automatically when you stop.")


def on_stop(event: dict) -> None:
    session = event.get("session_id") or "unknown"
    root = ks.project_dir(event.get("cwd"))
    with ks.board(root) as data:
        card_id = data["sessions"].get(session)
        for card in data["cards"]:
            if card["id"] == card_id and card["status"] == "doing":
                ks.move_card(data, card_id, "review")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        if mode == "prompt":
            context = on_prompt(event)
            if context:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                         "additionalContext": context}}))
        elif mode == "stop":
            on_stop(event)
    except Exception as exc:  # never block the user's prompt or Claude's stop
        print(f"kanban hook: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
