"""Markdown-backed kanban: the board is a view over .SDD/specs/.

- Ticket   = a spec folder .SDD/specs/<slug>/ ; its README.md frontmatter holds `status: <stage>`.
- Stages   = the ADLC: discovery, architect, approval, developer, qa, demo, e2e, done (the board's columns).
- Sub-task = prd/*.md and tasks/*.md inside the ticket, each with `status: todo|doing|blocked|done`.
- Progress = markdown checkboxes (`- [ ]` / `- [x]`) in those files.
Folders starting with "_" or "." are not tickets. Standard library only; writes are atomic and never delete files.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import tempfile
from pathlib import Path

STAGES = ("discovery", "architect", "approval", "developer", "qa", "demo", "e2e", "done")
STAGE_LABELS = {"discovery": "Discovery", "architect": "Architect", "approval": "Approval", "developer": "Developer",
                "qa": "QA", "demo": "Demo", "e2e": "E2E", "done": "Done"}
SUB_STATUSES = ("todo", "doing", "blocked", "done")
SUB_DIRS = ("prd", "tasks")
CHECK_RE = re.compile(r"^(\s*[-*] \[)([ xX])(\] )(.*)$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def today() -> str:
    return _dt.date.today().isoformat()


def project_dir(start: str | os.PathLike | None = None) -> Path:
    """KANBAN_PROJECT_DIR, CLAUDE_PROJECT_DIR, else walk up to the first folder with .SDD/, .claude/ or .git."""
    for var in ("KANBAN_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(var)
        if value and Path(value).is_dir():
            return Path(value).resolve()
    here = Path(start or os.getcwd()).resolve()
    for folder in (here, *here.parents):
        if any((folder / marker).exists() for marker in (".SDD", ".claude", ".git")):
            return folder
    return here


def specs_dir(root: Path) -> Path:
    return root / ".SDD" / "specs"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60].strip("-") or "ticket"


# ---------------------------------------------------------------- frontmatter
def split_frontmatter(text: str) -> tuple[dict, list[str], str]:
    """Return (fields, raw frontmatter lines, body). Only simple `key: value` lines are parsed."""
    if not text.startswith("---\n"):
        return {}, [], text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, [], text
    raw = text[4:end].splitlines()
    body = text[end + 4:].lstrip("\n")
    fields = {}
    for line in raw:
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2).split(" #", 1)[0].strip().strip('"').strip("'")
    return fields, raw, body


def set_fields(text: str, updates: dict) -> str:
    """Set frontmatter keys, keeping every other line and the key order; add frontmatter if missing."""
    fields, raw, body = split_frontmatter(text)
    lines = list(raw)
    for key, value in updates.items():
        for i, line in enumerate(lines):
            if re.match(rf"^{re.escape(key)}:", line):
                lines[i] = f"{key}: {value}"
                break
        else:
            lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body


def write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- checkboxes
def checkboxes(text: str) -> list[tuple[int, bool, str]]:
    """(line index, done, label) for every checkbox outside code fences."""
    out, fenced = [], False
    for i, line in enumerate(text.splitlines()):
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else CHECK_RE.match(line)
        if m:
            out.append((i, m.group(2) != " ", m.group(4).strip()))
    return out


def set_checkbox(text: str, item: int | str, done: bool = True) -> tuple[str, str]:
    """Tick/untick a checkbox by 1-based number or by (case-insensitive) label prefix. Returns (text, label)."""
    boxes = checkboxes(text)
    if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
        n = int(item)
        if not 1 <= n <= len(boxes):
            raise ValueError(f"there are {len(boxes)} checkbox(es); item is 1-based")
        target = boxes[n - 1]
    else:
        want = str(item).strip().lower()
        matches = [b for b in boxes if b[2].lower().startswith(want)]
        if len(matches) != 1:
            raise ValueError(f"{len(matches)} checkbox(es) start with {item!r}; use the number instead")
        target = matches[0]
    lines = text.splitlines(keepends=True)
    m = CHECK_RE.match(lines[target[0]].rstrip("\n"))
    ending = "\n" if lines[target[0]].endswith("\n") else ""
    lines[target[0]] = f"{m.group(1)}{'x' if done else ' '}{m.group(3)}{m.group(4)}{ending}"
    return "".join(lines), target[2]


# ---------------------------------------------------------------- tickets
def safe_path(root: Path, rel: str) -> Path:
    """Resolve a path inside .SDD/specs; refuse anything outside it."""
    base = specs_dir(root).resolve()
    path = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
    if base != path and base not in path.parents:
        raise ValueError(f"{rel} is not inside .SDD/specs")
    return path


def ticket_readme(root: Path, ticket: str) -> Path:
    path = specs_dir(root) / ticket / "README.md"
    if not path.exists():
        raise KeyError(f"no ticket {ticket} (expected {path.relative_to(root)})")
    return path


def _progress(text: str) -> dict:
    boxes = checkboxes(text)
    return {"done": sum(b[1] for b in boxes), "total": len(boxes)}


def read_ticket(root: Path, folder: Path) -> dict:
    readme = folder / "README.md"
    text = readme.read_text() if readme.exists() else ""
    fields, _, body = split_frontmatter(text)
    title = fields.get("title") or next((l[2:].strip() for l in body.splitlines() if l.startswith("# ")), folder.name)
    status = fields.get("status", "").lower()
    subtasks = []
    for sub in SUB_DIRS:
        for md in sorted((folder / sub).glob("*.md")) if (folder / sub).is_dir() else []:
            stext = md.read_text()
            sfields, _, sbody = split_frontmatter(stext)
            stitle = sfields.get("title") or next((l[2:].strip() for l in sbody.splitlines() if l.startswith("# ")),
                                                  md.stem)
            subtasks.append({"path": md.relative_to(root).as_posix(), "title": stitle,
                             "status": sfields.get("status", "todo").lower(), **_progress(stext)})
    files = sorted(p.relative_to(root).as_posix() for p in folder.rglob("*.md") if p.is_file())
    return {"id": folder.name, "title": title, "status": status if status in STAGES else "discovery",
            "status_set": status in STAGES, "path": readme.relative_to(root).as_posix() if readme.exists() else None,
            "updated": fields.get("updated", ""), "owner": fields.get("owner", ""),
            "progress": _progress(text), "subtasks": subtasks, "files": files}


def list_tickets(root: Path) -> list[dict]:
    base = specs_dir(root)
    if not base.is_dir():
        return []
    return [read_ticket(root, f) for f in sorted(base.iterdir())
            if f.is_dir() and not f.name.startswith(("_", "."))]


def ticket_template(title: str, summary: str) -> str:
    stages = "\n".join(f"- [ ] {STAGE_LABELS[s]}" for s in STAGES if s != "done")
    return (f"---\ntitle: {title}\nstatus: discovery\nupdated: {today()}\n---\n\n# {title}\n\n"
            f"{summary.strip() or '<one paragraph: the goal, stated neutrally>'}\n\n"
            f"## Progress\n{stages}\n\n## Files\n- Discovery: `01-discovery.md` (to be written)\n")


def create_ticket(root: Path, title: str, slug: str | None = None, summary: str = "") -> dict:
    slug = slugify(slug or title)
    folder = specs_dir(root) / slug
    if folder.exists():
        raise ValueError(f"ticket {slug} already exists: {folder.relative_to(root)}")
    folder.mkdir(parents=True)
    write_atomic(folder / "README.md", ticket_template(title.strip(), summary))
    return read_ticket(root, folder)


def move_ticket(root: Path, ticket: str, stage: str) -> dict:
    stage = stage.lower()
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    path = ticket_readme(root, ticket)
    text = set_fields(path.read_text(), {"status": stage, "updated": today()})
    # tick the Progress checkboxes of every stage before the new one (never unticks)
    for s in STAGES[:STAGES.index(stage)]:
        try:
            text, _ = set_checkbox(text, STAGE_LABELS[s], True)
        except ValueError:
            pass
    write_atomic(path, text)
    return read_ticket(root, path.parent)


def add_subtask(root: Path, ticket: str, title: str, status: str = "todo", checklist: list[str] | None = None) -> str:
    folder = ticket_readme(root, ticket).parent / "tasks"
    folder.mkdir(exist_ok=True)
    number = 1 + max((int(m.group(1)) for p in folder.glob("*.md") if (m := re.match(r"(\d+)-", p.name))), default=0)
    path = folder / f"{number:02d}-{slugify(title)}.md"
    items = "\n".join(f"- [ ] {c.strip()}" for c in (checklist or []) if c.strip())
    write_atomic(path, f"---\ntitle: {title.strip()}\nstatus: {status}\nupdated: {today()}\n---\n\n# {title.strip()}\n\n"
                       + (f"{items}\n" if items else ""))
    return path.relative_to(root).as_posix()


def set_status(root: Path, rel: str, status: str) -> str:
    """Set `status:` of a sub-task file (prd/*.md, tasks/*.md) or, with a stage, of a ticket README."""
    path = safe_path(root, rel)
    status = status.lower()
    allowed = STAGES if path.name == "README.md" and path.parent.parent == specs_dir(root).resolve() else SUB_STATUSES
    if status not in allowed:
        raise ValueError(f"status for {rel} must be one of {', '.join(allowed)}")
    write_atomic(path, set_fields(path.read_text(), {"status": status, "updated": today()}))
    return f"{rel}: status {status}"


def check(root: Path, rel: str, item: int | str, done: bool = True) -> str:
    path = safe_path(root, rel)
    text, label = set_checkbox(path.read_text(), item, done)
    write_atomic(path, text)
    return f"{rel}: [{'x' if done else ' '}] {label}"


def render_board(root: Path) -> str:
    tickets = list_tickets(root)
    lines = []
    for stage in STAGES:
        in_stage = [t for t in tickets if t["status"] == stage]
        lines.append(f"## {STAGE_LABELS[stage]} ({len(in_stage)})")
        for t in in_stage:
            note = "" if t["status_set"] else " (no status in README frontmatter)"
            lines.append(f"- {t['id']}: {t['title']} [{t['progress']['done']}/{t['progress']['total']}]{note}"
                         f" — {t['path'] or 'no README.md'}")
            for s in t["subtasks"]:
                lines.append(f"  - {s['status']}: {s['title']} [{s['done']}/{s['total']}] — {s['path']}")
    return "\n".join(lines) if tickets else "No tickets yet: .SDD/specs/ has no ticket folders."
