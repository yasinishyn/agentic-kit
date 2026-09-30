"""Terminal module contract for v0.3.1 (PRD-06, ADR-005, architecture §4.1): ui/modules/terminal.js sets
window.kanban.connectClaude before it registers the "terminal" project panel, targets the drawer's Terminal tab, uses
the PRD-05 classes/tokens instead of colour or pixel literals, and connectClaude starts or reveals the project's
channels terminal (one pty per project, the same host node in the drawer and the project sheet).

Static checks read terminal.js; the behavioural checks run it in node against a tiny fake DOM, fake Tauri IPC and a
fake xterm (skipped when node is missing). Stdlib only."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
import unittest

import helpers

TERMINAL = helpers.PLUGIN / "ui" / "modules" / "terminal.js"
HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
FUNC = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(")
NAMED = re.compile(r"(?<![\w-])(?:white|black|red|green|blue|gray|grey|orange|yellow|purple|pink|silver)(?![\w-])")
PX = re.compile(r"\b\d+(?:\.\d+)?px\b")


def source() -> str:
    text = TERMINAL.read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


# A fake DOM just big enough for terminal.js: nodes with parent/children, attributes, listeners and isConnected.
HARNESS = r"""
const log = [];
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.parent = null; this.attrs = {}; this.listeners = {};
                     this.style = {}; this.dataset = {}; this.hidden = false; this.disabled = false; this._text = "";
                     this.className = ""; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  append(...nodes) { for (const n of nodes) { if (n.parent) n.parent.children = n.parent.children.filter((c) => c !== n);
                                               n.parent = this; this.children.push(n); } }
  replaceChildren(...nodes) { for (const c of this.children) c.parent = null; this.children = []; this.append(...nodes); }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; } }
  contains(n) { for (let p = n; p; p = p.parent) if (p === this) return true; return false; }
  get isConnected() { for (let p = this; p; p = p.parent) if (p === document.documentElement) return true; return false; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  click() { for (const f of this.listeners.click || []) f({}); }
  focus() { log.push(["focus", this.tag]); }
  scrollIntoView() { log.push(["scroll", this.className]); }
  getContext() { return {font: "", measureText: () => ({width: 7.2})}; }
  get clientWidth() { return 600; }
}
const document = {documentElement: new Node("html"), createElement: (t) => new Node(t)};
document.head = new Node("head"); document.body = new Node("body");
document.documentElement.append(document.head, document.body);
const sheet = new Node("aside"); sheet.hidden = true; sheet.id = "project-sheet";
const observers = [];
globalThis.MutationObserver = class { constructor(f) { observers.push(f); } observe() {} };
const drawer = new Node("aside"); drawer.hidden = true;
const chip = new Node("button"); chip.id = "conn-chip";
// app.js openSheet/closeSheet: the sheet and the drawer are never open together
chip.addEventListener("click", () => { sheet.hidden = !sheet.hidden; if (!sheet.hidden) drawer.hidden = true;
                                       log.push(["chip", sheet.hidden]); for (const f of observers) f([]); });
const panels = new Node("div"); sheet.append(panels);
const drawerBody = new Node("div"); drawer.append(drawerBody);
document.body.append(chip, sheet, drawer);
document.getElementById = (id) => ({"project-sheet": sheet, "conn-chip": chip, "drawer": drawer})[id] || null;
function el(tag, attrs, children) {
  const node = new Node(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "text") node.textContent = v; else if (k === "className") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children || [])) if (c) node.append(c);
  return node;
}
const listeners = {};
let current = "p1";
let openCount = 0;
let failNext = null;
const window = {
  requestAnimationFrame: (f) => f(), open: () => {},
  getComputedStyle: () => ({paddingLeft: "6px", paddingRight: "6px", getPropertyValue: () => ""}),
  KanbanBoard: {el},
  __TAURI__: {
    core: {invoke: async (cmd, args) => {
      log.push(["invoke", cmd, args]);
      if (cmd === "term_open") { if (failNext) { const e = failNext; failNext = null; throw e; } return ++openCount; }
      return null;
    }},
    event: {listen: (name, f) => { listeners[name] = f; }},
  },
  Terminal: class { constructor(o) { this.opts = o; } open(h) { this.host = h; } write() {} focus() { log.push(["termfocus"]); }
                    blur() {} resize() {} refresh() {} onData() {} attachCustomKeyEventHandler() {} },
};
const registered = [];
window.kanban = {
  project: () => current,
  addDrawerPanel(id, title, render, opts) { registered.push({kind: "drawer", id, title, render, opts,
                                                             connect: typeof window.kanban.connectClaude}); },
  addProjectPanel(id, title, render) { registered.push({kind: "project", id, title, render,
                                                        connect: typeof window.kanban.connectClaude}); },
};
globalThis.window = window; globalThis.document = document;
globalThis.ResizeObserver = class { observe() {} };
globalThis.getComputedStyle = window.getComputedStyle;
"""


def run_node(body: str):
    script = (HARNESS + "\n" + TERMINAL.read_text() + "\n(async () => {\n" + textwrap.dedent(body) +
              "\n})().then((r) => process.stdout.write(JSON.stringify(r)), (e) => { console.error(e); process.exit(3); });")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


class StaticTests(unittest.TestCase):
    def test_terminal_sets_connect_before_panel(self):
        src = source()
        assign = re.search(r"\bK\.connectClaude\s*=", src)
        panel = re.search(r"\bK\.addProjectPanel\(\s*\"terminal\"\s*,\s*\"Terminal\"", src)
        self.assertIsNotNone(assign, "terminal.js must set window.kanban.connectClaude")
        self.assertIsNotNone(panel, 'terminal.js must call addProjectPanel("terminal", "Terminal", …)')
        self.assertLess(assign.start(), panel.start())

    def test_terminal_uses_tokens(self):
        src = source()
        for pattern in (HEX, FUNC, NAMED, PX):
            self.assertEqual(pattern.findall(src), [], f"{pattern.pattern} in terminal.js")
        self.assertNotRegex(src, r"\.style\b", "inline styles: use the PRD-05 classes and tokens")
        self.assertIn('"term-host"', src)
        self.assertIn('"term-bar"', src)
        self.assertIn('"muted"', src)
        self.assertIn("--term-bg", src)

    def test_terminal_drawer_tab_hint(self):
        self.assertRegex(source(), r'K\.addDrawerPanel\(\s*"terminal"\s*,\s*"Terminal"\s*,\s*\w+\s*,\s*'
                                   r'\{\s*tab:\s*"terminal"\s*\}\s*\)')


@unittest.skipUnless(shutil.which("node"), "node not installed")
class BehaviourTests(unittest.TestCase):
    def test_registration_order_and_tab(self):
        got = run_node("""
            return registered.map((r) => [r.kind, r.id, r.title, r.opts || null, r.connect]);
        """)
        self.assertEqual(got, [["drawer", "terminal", "Terminal", {"tab": "terminal"}, "function"],
                               ["project", "terminal", "Terminal", None, "function"]])

    def test_connect_claude_starts_then_reveals(self):
        got = run_node("""
            const proj = registered.find((r) => r.kind === "project");
            const box = el("div"); panels.append(box);
            proj.render(box, {id: "p1", name: "One", root: "/work/one"});
            const first = await window.kanban.connectClaude("p1");
            const opens = log.filter((e) => e[0] === "invoke" && e[1] === "term_open").map((e) => e[2]);
            const second = await window.kanban.connectClaude("p1");
            const opens2 = log.filter((e) => e[0] === "invoke" && e[1] === "term_open").length;
            return {first, second, preset: opens[0].preset, projectId: opens[0].projectId, opens2,
                    sheetOpen: !sheet.hidden, hostInSheet: sheet.contains(box.children[1]),
                    hostClass: box.children[1].className, barClass: box.children[0].className};
        """)
        self.assertEqual(got, {"first": "started", "second": "revealed", "preset": "claude-channels",
                               "projectId": "p1", "opens2": 1, "sheetOpen": True, "hostInSheet": True,
                               "hostClass": "term-host", "barClass": "term-bar"})

    def test_connect_claude_rejections(self):
        got = run_node("""
            const out = {};
            const proj = registered.find((r) => r.kind === "project");
            const box = el("div"); panels.append(box);
            proj.render(box, {id: "p1", name: "One", root: "/work/one"});
            try { await window.kanban.connectClaude("p2"); out.other = "resolved"; } catch (e) { out.other = e.message; }
            out.opensAfterOther = log.filter((e) => e[1] === "term_open").length;
            failNext = "the project folder /work/one does not exist";
            try { await window.kanban.connectClaude("p1"); out.fail = "resolved"; } catch (e) { out.fail = e.message; }
            // a shell is running: reveal it, say why Claude was not started
            const shellBtn = box.children[0].children[0];
            shellBtn.click();
            await new Promise((r) => setTimeout(r, 0));
            try { await window.kanban.connectClaude("p1"); out.shell = "resolved"; } catch (e) { out.shell = e.message; }
            out.status = box.children[0].children[3].textContent;
            out.presets = log.filter((e) => e[1] === "term_open").map((e) => e[2].preset);
            return out;
        """)
        self.assertEqual(got["other"], "Switch to the project first")
        self.assertEqual(got["opensAfterOther"], 0)
        self.assertEqual(got["fail"], "Could not start the terminal: the project folder /work/one does not exist")
        self.assertEqual(got["shell"], "A shell is open here; close it to start Claude with channels")
        self.assertEqual(got["status"], "A shell is open here; close it to start Claude with channels")
        self.assertEqual(got["presets"], ["claude-channels", "shell"])

    def test_same_host_in_drawer_and_sheet(self):
        got = run_node("""
            const proj = registered.find((r) => r.kind === "project");
            const drawer = registered.find((r) => r.kind === "drawer");
            const sheetBox = el("div"); panels.append(sheetBox);
            const drawerBox = el("div"); drawerBody.append(drawerBox);
            proj.render(sheetBox, {id: "p1", name: "One", root: "/work/one"});
            const host = sheetBox.children[1];
            drawer.render(drawerBox, {id: "T-1"});
            const moved = drawerBox.children[1] === host && !sheetBox.contains(host);
            // Connect from the welcome while the host sits in the (closed) drawer: it comes back to the sheet
            await window.kanban.connectClaude("p1");
            const back = sheetBox.contains(host);
            current = "p2";
            proj.render(sheetBox, {id: "p2", name: "Two", root: "/work/two"});
            const other = sheetBox.children[1] !== host;
            proj.render(sheetBox, null);
            return {moved, back, other, none: sheetBox.textContent};
        """)
        self.assertEqual(got, {"moved": True, "back": True, "other": True, "none": "No project selected."})

    def test_board_reload_keeps_open_drawer_terminal(self):
        got = run_node("""
            const proj = registered.find((r) => r.kind === "project");
            const drawerPanel = registered.find((r) => r.kind === "drawer");
            const sheetBox = el("div"); panels.append(sheetBox);
            proj.render(sheetBox, {id: "p1", name: "One", root: "/work/one"});
            const host = sheetBox.children[1];
            // a ticket's Terminal tab is open; loadBoard() re-renders the drawer, then every project panel
            drawer.hidden = false;
            const drawerBox = el("div"); drawerBody.append(drawerBox);
            drawerPanel.render(drawerBox, {id: "T-1"});
            proj.render(sheetBox, {id: "p1", name: "One", root: "/work/one"});
            const kept = drawerBox.contains(host);
            const note = sheetBox.textContent;
            const buttonsInDrawer = drawerBox.children[0].className;
            // opening the project sheet (closes the drawer) brings the terminal into the sheet
            chip.click();
            return {kept, note, buttonsInDrawer, inSheet: sheetBox.contains(host), sheetBar: sheetBox.children[0].className};
        """)
        self.assertEqual(got, {"kept": True, "note": "This project's terminal is open in the ticket panel.",
                               "buttonsInDrawer": "term-bar", "inSheet": True, "sheetBar": "term-bar"})

    def test_claude_not_found_links_troubleshooting(self):
        got = run_node("""
            const proj = registered.find((r) => r.kind === "project");
            const box = el("div"); panels.append(box);
            proj.render(box, {id: "p1", name: "One", root: "/work/one"});
            const id = openCount + 1;
            await window.kanban.connectClaude("p1");
            listeners["term:exit"]({payload: {id, code: 127}});
            const status = box.children[0].children[3];
            const link = status.children.find((c) => c.tag === "a");
            const text = status.textContent;
            const again = await window.kanban.connectClaude("p1");
            return {text, href: link && link.attrs.href, target: link && link.attrs.target, again};
        """)
        self.assertTrue(got["text"].startswith("Claude was not found on the login shell PATH"), got["text"])
        self.assertTrue(got["href"].startswith("https://github.com/yasinishyn/agentic-kit/blob/main/plugins/kanban/"
                                               "README.md#"), got["href"])
        self.assertEqual(got["target"], "_blank")
        self.assertEqual(got["again"], "started")


if __name__ == "__main__":
    unittest.main()
