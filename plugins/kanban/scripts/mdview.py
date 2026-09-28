"""Small, safe markdown subset → HTML for the board's viewer (standard library only).

Every piece of source text is HTML-escaped before the few supported constructs are added back (headings, lists,
checkboxes, tables, fences, `code`, **bold**, links). Only http(s) links and relative .md links become anchors; any
other target (javascript:, data:, …) is dropped and only its label is kept. Used by the daemon (fragment inserted in
the drawer, .md links carry data-md) and by server.py local mode (full page, .md links go to /view?path=).
"""
from __future__ import annotations

import html
import os
import re
import urllib.parse
from pathlib import Path
from typing import Callable

import kanban_md as km

PAGE_STYLE = ("body{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:16px;color:#1d2330}"
              "pre{background:#f4f5f7;padding:8px;overflow:auto}table{border-collapse:collapse;margin:8px 0;"
              "display:block;overflow-x:auto}th,td{border:1px solid #d0d5dd;padding:4px 8px;text-align:left;"
              "vertical-align:top}th{background:#f4f5f7}code{background:#f4f5f7;padding:0 3px}"
              ".fm{color:#667085;font-size:13px}nav{font-size:13px;margin-bottom:8px}"
              "@media(prefers-color-scheme:dark){body{background:#12151b;color:#e6e9ef}pre,code{background:#232a35}}")


def view_link(rel: str) -> str:
    """Local mode: a relative .md link opens /view?path=<rel>."""
    return f'href="/view?path={html.escape(urllib.parse.quote(rel))}"'


def data_link(rel: str) -> str:
    """Daemon UI: a relative .md link is handled by app.js (no navigation)."""
    return f'href="#" data-md="{html.escape(rel, quote=True)}"'


def _inline(text: str, base: Path, root: Path, md_link: Callable[[str], str]) -> str:
    out = html.escape(text, quote=True)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)

    def link(m):
        label, target = m.group(1), html.unescape(m.group(2))
        if re.match(r"^https?://", target, re.I):
            return f'<a href="{html.escape(target, quote=True)}" rel="noopener noreferrer" target="_blank">{label}</a>'
        path_part = target.split("#", 1)[0]
        if path_part.endswith(".md") and not re.match(r"^[a-z][a-z0-9+.-]*:", path_part, re.I):
            rel = Path(os.path.relpath((base / path_part).resolve(), root)).as_posix()
            return f"<a {md_link(rel)}>{label}</a>"
        return label
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, out)


def render_fragment(path: Path, root: Path, md_link: Callable[[str], str] = data_link) -> str:
    """The body HTML of a markdown file (frontmatter shown as a line of key: value)."""
    path, root = Path(path), Path(root)
    fields, _, body = km.split_frontmatter(path.read_text())
    base, parts, fenced, in_list, table = path.parent, [], False, False, []

    def inline(text):
        return _inline(text, base, root, md_link)

    def flush_table():
        if not table:
            return
        rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in table]
        rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]  # drop |---| separators
        if rows:
            head, *body_rows = rows
            parts.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head)
                         + "</tr></thead><tbody>"
                         + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body_rows)
                         + "</tbody></table>")
        table.clear()
    if fields:
        parts.append("<p class=fm>" + " · ".join(f"<b>{html.escape(k)}</b>: {html.escape(v)}"
                                                  for k, v in fields.items()) + "</p>")
    for line in body.splitlines():
        if km.FENCE_RE.match(line):
            flush_table()
            parts.append("</pre>" if fenced else "<pre>")
            fenced = not fenced
            continue
        if fenced:
            parts.append(html.escape(line))
            continue
        if line.lstrip().startswith("|"):
            table.append(line)
            continue
        flush_table()
        item = re.match(r"^(\s*)[-*] (.*)$", line)
        if item and not in_list:
            parts.append("<ul>")
            in_list = True
        if not item and in_list:
            parts.append("</ul>")
            in_list = False
        if item:
            box = km.CHECK_RE.match(line)
            content = (f'<input type=checkbox disabled {"checked" if box.group(2) != " " else ""}> '
                       + inline(box.group(4))) if box else inline(item.group(2))
            parts.append(f"<li>{content}</li>")
        else:
            m = re.match(r"^(#{1,6}) (.*)$", line)
            if m:
                n = len(m.group(1))
                parts.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            elif line.strip():
                parts.append(f"<p>{inline(line)}</p>")
    flush_table()
    if in_list:
        parts.append("</ul>")
    if fenced:
        parts.append("</pre>")
    return "\n".join(parts)


def render_page(path: Path, root: Path, editor_url: str) -> str:
    """Local mode (server.py /view): a standalone page with a back link and an editor link."""
    path, root = Path(path), Path(root)
    rel = path.relative_to(root).as_posix()
    editor = html.escape(editor_url.format(path=str(path)), quote=True)
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(path.name)}</title>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<style>{PAGE_STYLE}</style>"
            f"<nav><a href='/'>← Board</a> · <code>{html.escape(rel)}</code> · <a href='{editor}'>Open in editor</a></nav>"
            + render_fragment(path, root, view_link))
