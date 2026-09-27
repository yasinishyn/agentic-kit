"""Tiny file-backed kanban board shared by the hooks, the MCP server and the web UI.

The board lives in <project>/.kanban/board.json. It holds prompt text, so the folder ignores
itself (.kanban/.gitignore = "*") and is never committed. Standard library only.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
import tempfile
from pathlib import Path

STATUSES = ("todo", "doing", "review", "done")
STATUS_LABELS = {"todo": "To do", "doing": "In progress", "review": "Review", "done": "Done"}
MAX_TITLE = 120


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def project_dir(start: str | os.PathLike | None = None) -> Path:
    """Resolve the project root.

    Order: KANBAN_PROJECT_DIR, CLAUDE_PROJECT_DIR, then walk up from `start` (or cwd) to the first
    folder that has .kanban/, .claude/ or .git; fall back to the starting folder.
    """
    for var in ("KANBAN_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(var)
        if value and Path(value).is_dir():
            return Path(value).resolve()
    here = Path(start or os.getcwd()).resolve()
    for folder in (here, *here.parents):
        if any((folder / marker).exists() for marker in (".kanban", ".claude", ".git")):
            return folder
    return here


def board_dir(root: Path) -> Path:
    folder = root / ".kanban"
    folder.mkdir(exist_ok=True)
    ignore = folder / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")  # the board holds prompt text: never commit it
    return folder


def _empty() -> dict:
    return {"version": 1, "next_id": 1, "cards": [], "sessions": {}}


@contextlib.contextmanager
def board(root: Path, write: bool = True):
    """Yield the board dict under an exclusive file lock; save atomically on exit when write=True."""
    folder = board_dir(root)
    path = folder / "board.json"
    with open(folder / ".lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text()) if path.exists() else _empty()
        except (OSError, ValueError):
            data = _empty()
        for key, value in _empty().items():
            data.setdefault(key, value)
        yield data
        if write:
            fd, tmp = tempfile.mkstemp(dir=folder, prefix=".board.", suffix=".tmp")
            with os.fdopen(fd, "w") as handle:
                json.dump(data, handle, indent=1, ensure_ascii=False)
            os.replace(tmp, path)


def _title(text: str) -> str:
    first = " ".join((text or "").strip().splitlines()[0].split()) if (text or "").strip() else "(empty)"
    return first if len(first) <= MAX_TITLE else first[: MAX_TITLE - 1] + "…"


def _find(data: dict, card_id: str) -> dict:
    for card in data["cards"]:
        if card["id"] == card_id:
            return card
    raise KeyError(f"no card {card_id}")


def add_card(data: dict, title: str, status: str = "todo", source: str = "claude",
             parent: str | None = None, note: str = "", session_id: str | None = None) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if parent:
        _find(data, parent)
    card = {
        "id": f"K-{data['next_id']}", "title": _title(title), "status": status, "source": source,
        "parent": parent, "note": note.strip(), "steps": [], "session_id": session_id,
        "created": now(), "updated": now(),
    }
    data["next_id"] += 1
    data["cards"].append(card)
    return card


def move_card(data: dict, card_id: str, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    card = _find(data, card_id)
    card["status"], card["updated"] = status, now()
    return card


def update_card(data: dict, card_id: str, title: str | None = None, note: str | None = None,
                add_step: str | None = None, complete_step: int | None = None) -> dict:
    card = _find(data, card_id)
    if title:
        card["title"] = _title(title)
    if note is not None:
        card["note"] = note.strip()
    if add_step:
        card["steps"].append({"text": _title(add_step), "done": False})
    if complete_step is not None:
        steps = card["steps"]
        if not 1 <= complete_step <= len(steps):
            raise ValueError(f"{card_id} has {len(steps)} step(s); complete_step is 1-based")
        steps[complete_step - 1]["done"] = True
    card["updated"] = now()
    return card


def delete_card(data: dict, card_id: str) -> None:
    _find(data, card_id)
    data["cards"] = [c for c in data["cards"] if c["id"] != card_id and c.get("parent") != card_id]


def render(data: dict, status: str | None = None) -> str:
    """Plain-text board for Claude: one section per column, children indented under parents."""
    lines = []
    for column in STATUSES:
        if status and column != status:
            continue
        cards = [c for c in data["cards"] if c["status"] == column]
        lines.append(f"## {STATUS_LABELS[column]} ({len(cards)})")
        for card in cards:
            done = sum(s["done"] for s in card["steps"])
            steps = f" [{done}/{len(card['steps'])} steps]" if card["steps"] else ""
            parent = f" (part of {card['parent']})" if card.get("parent") else ""
            lines.append(f"- {card['id']} {card['title']}{steps}{parent}")
    return "\n".join(lines)
