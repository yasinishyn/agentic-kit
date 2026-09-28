"""Pure domain rules of the kanban (architecture §2): no I/O, standard library only.

- Transitions (gate on entry only, Q14): entering EXECUTION from outside it needs a valid approval; moves inside it
  and backward moves are always allowed.
- Hand-off kinds, spec hash normalisation, approval record/line, run view state and the fixed hand-off templates.
"""
from __future__ import annotations

import hashlib
import re

STAGES = ("discovery", "architect", "approval", "developer", "qa", "demo", "e2e", "done")
STAGE_LABELS = {"discovery": "Discovery", "architect": "Architect", "approval": "Approval", "developer": "Developer",
                "qa": "QA", "demo": "Demo", "e2e": "E2E", "done": "Done"}
WORKING = frozenset({"discovery", "architect", "developer", "qa", "demo", "e2e"})
EXECUTION = frozenset({"developer", "qa", "demo", "e2e", "done"})
ACTORS = ("human (board)", "human (chat)", "claude", "runner")
APPROVAL_STATES = ("none", "valid", "changed")
RUN_LIVE = ("running", "waiting")
SPEC_PATTERNS = ("01-*.md", "02-*.md", "03-*.md", "adr/*.md", "prd/*.md", "OPEN-QUESTIONS.md")
APPROVAL_PREFIX = "Approved for execution by "
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
HANDOFF_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DROPPED_KEYS = ("status", "updated", "approved_by", "approved_at", "approved_hash")
_BOX_RE = re.compile(r"^(\s*[-*] \[)[xX](\] )", re.M)


def _stage(stage: str) -> str:
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    return stage


# ---------------------------------------------------------------- transitions and hand-offs
def transition_allowed(src: str, dst: str, approval: str | None) -> str:
    """'ok' | 'approval_required' | 'spec_changed'. `approval` is an approval_state(): none, valid or changed."""
    src, dst, approval = _stage(src), _stage(dst), approval or "none"
    if approval not in APPROVAL_STATES:
        raise ValueError(f"approval must be one of {', '.join(APPROVAL_STATES)}")
    if STAGES.index(dst) <= STAGES.index(src) or dst not in EXECUTION or src in EXECUTION:
        return "ok"
    return {"valid": "ok", "changed": "spec_changed"}.get(approval, "approval_required")


def handoff_kind(src: str, dst: str, actor: str) -> str | None:
    """'start' (forward into WORKING), 'rework' (backward into WORKING) or None; only board moves by a human count."""
    src, dst = _stage(src), _stage(dst)
    if actor != "human (board)" or src == dst or dst not in WORKING:
        return None
    return "start" if STAGES.index(dst) > STAGES.index(src) else "rework"


# ---------------------------------------------------------------- spec hash and approval
def normalise_spec_text(text: str) -> str:
    """Drop volatile frontmatter keys and approval lines; render every checkbox unchecked."""
    lines = text.splitlines()
    start = 0
    if lines and lines[0] == "---":
        end = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
        if end is not None:
            keep = [l for l in lines[1:end] if not re.match(rf"^({'|'.join(_DROPPED_KEYS)}):", l)]
            lines = ["---", *keep, *lines[end:]]
            start = len(keep) + 2
    lines = lines[:start] + [l for l in lines[start:] if not l.startswith(APPROVAL_PREFIX)]
    # blank-line runs collapse, so inserting the approval line with its blank separator never changes the hash
    text = re.sub(r"\n(?:[ \t]*\n)+", "\n\n", "\n".join(lines))
    return _BOX_RE.sub(r"\1 \2", text)


def spec_hash_from_texts(texts: dict[str, str]) -> str:
    """sha256 hex over the sorted, normalised spec files ({relative posix name: text})."""
    digest = hashlib.sha256()
    for name in sorted(texts):
        digest.update(f"{name}\0{normalise_spec_text(texts[name])}\0".encode())
    return digest.hexdigest()


def approval_record(fields: dict) -> dict | None:
    """The approval recorded in README frontmatter, or None."""
    if not fields.get("approved_hash"):
        return None
    return {"by": fields.get("approved_by", ""), "at": fields.get("approved_at", ""), "hash": fields["approved_hash"]}


def approval_valid(fields: dict, current_hash: str) -> bool:
    record = approval_record(fields)
    return bool(record and current_hash and record["hash"] == current_hash)


def approval_state(fields: dict, current_hash: str) -> str:
    """'none' (no record), 'valid' (hash matches) or 'changed' (spec edited since approval)."""
    if approval_record(fields) is None:
        return "none"
    return "valid" if approval_valid(fields, current_hash) else "changed"


def approval_line(actor: str, date: str, spec_hash: str) -> str:
    if actor not in ACTORS:
        raise ValueError(f"actor must be one of {', '.join(ACTORS)}")
    return f"{APPROVAL_PREFIX}{actor} on {date} (spec {spec_hash[:12]})"


def place_approval_line(text: str, line: str) -> str:
    """Replace an existing approval line (removing duplicates) or insert one after the header table (else after H1)."""
    lines = text.splitlines()
    tail = "\n" if text.endswith("\n") or not text else ""
    start = 0
    if lines and lines[0] == "---":
        start = next((i + 1 for i in range(1, len(lines)) if lines[i] == "---"), 0)
    found = [i for i in range(start, len(lines)) if lines[i].startswith(APPROVAL_PREFIX)]
    if found:
        lines[found[0]] = line
        lines = [l for i, l in enumerate(lines) if i not in found[1:]]
        return "\n".join(lines) + tail
    at = start
    h1 = next((i for i in range(start, len(lines)) if lines[i].startswith("# ")), None)
    if h1 is not None:
        at = h1 + 1
        i = at
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and lines[i].startswith("|"):
            while i < len(lines) and lines[i].startswith("|"):
                i += 1
            at = i
    else:
        while at < len(lines) and not lines[at].strip():
            at += 1
    block = ([""] if at > start and lines[at - 1].strip() else []) + [line]
    block += [""] if at < len(lines) and lines[at].strip() else []
    return "\n".join(lines[:at] + block + lines[at:]) + tail


# ---------------------------------------------------------------- runs
def view_state(run: dict, now: float, ttl: float) -> str:
    """The run's status, or 'stale' for running/waiting runs whose heartbeat is older than ttl seconds."""
    status = run.get("status", "")
    beat = run.get("heartbeat_at")
    if status in RUN_LIVE and beat is not None and now - float(beat) > ttl:
        return "stale"
    return status


# ---------------------------------------------------------------- hand-off templates (slug and stage names only)
def _handoff_fields(handoff: dict) -> tuple[str, str, str, str, str]:
    slug, hid = str(handoff.get("ticket", "")), str(handoff.get("handoff_id", ""))
    if not SLUG_RE.match(slug):
        raise ValueError("ticket slug must match ^[a-z0-9][a-z0-9-]{0,59}$")
    if not HANDOFF_ID_RE.match(hid):
        raise ValueError("handoff_id must be letters, digits, '-' or '_'")
    kind = handoff.get("kind")
    if kind not in ("start", "rework"):
        raise ValueError("kind must be start or rework")
    return slug, _stage(handoff.get("stage")), _stage(handoff.get("from_stage")), hid, kind


def _instructions(slug: str, stage: str, src: str, hid: str, kind: str) -> str:
    verb = "moved back" if kind == "rework" else "moved"
    text = (f"The developer {verb} ticket {slug} from {STAGE_LABELS[src]} to {STAGE_LABELS[stage]} on the kanban "
            f"board (hand-off {hid}). First call kanban_start with ticket \"{slug}\" and handoff_id \"{hid}\"; "
            f"stop if it does not answer ok. ")
    if stage == "developer":
        text += ("Then verify the approval with the kanban_approval tool; if it is not valid, stop and report that "
                 "approval is required instead of writing code. ")
    if kind == "rework":
        text += f"This is rework: read what changed in the spec before redoing the {STAGE_LABELS[stage]} stage. "
    return text + (f"Work the {STAGE_LABELS[stage]} stage of .SDD/specs/{slug}/ with the sdd skill, "
                   "then call kanban_finish.")


def channel_event(handoff: dict) -> dict:
    """Fixed-template channel notification; never includes the ticket title or other free text."""
    slug, stage, src, hid, kind = _handoff_fields(handoff)
    return {"content": _instructions(slug, stage, src, hid, kind),
            "meta": {"ticket": slug, "stage": stage, "from_stage": src, "handoff_id": hid, "kind": kind}}


def headless_prompt(handoff: dict) -> str:
    """Fixed-template prompt for a headless `claude -p` run; never includes the ticket title or other free text."""
    slug, stage, src, hid, kind = _handoff_fields(handoff)
    return ("You are running headless for the kanban board, with no user to ask. "
            + _instructions(slug, stage, src, hid, kind))
