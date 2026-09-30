"""UI contract for v0.3.1 (PRD-05, ADR-005, architecture §4.1): design tokens and contrast, the extended plug-in API
(addDrawerPanel {tab}, addProjectPanel, feature-detected connectClaude), header controls, onboarding and the Add
project dry run, the queued-card explanation, and the strict-CSP rules (no inline script, one innerHTML sink).

Static checks read the UI sources; behavioural checks of the pure helpers in board.js run them in node (skipped when
node is missing); the served-UI check uses a throwaway daemon (tests/helpers.py). Stdlib only."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest

import helpers

UI = helpers.PLUGIN / "ui"
MODULES = UI / "modules"
# PRD-05's files (terminal.js belongs to PRD-06 and is checked by its own contract test)
PRD05_FILES = [UI / "board.css", UI / "app.js", UI / "board.js", UI / "index.html",
               MODULES / "runs.js", MODULES / "runs.css", MODULES / "specs.js", MODULES / "specs.css",
               MODULES / "transcript.js"]
HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
FUNC = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(")
NAMED = re.compile(r"(?<![\w-])(?:white|black|red|green|blue|gray|grey|orange|yellow|purple|pink|silver)(?![\w-])")
TABS = ["overview", "spec", "runs", "terminal", "git"]


def read(path) -> str:
    return path.read_text()


def css_token_blocks(css: str) -> tuple[str, dict, dict]:
    """(css without the token blocks, light tokens, dark tokens). Token blocks: `:root { … }` at top level and
    `:root { … }` inside `@media (prefers-color-scheme: dark) { … }`."""
    light, dark = {}, {}

    def tokens(block):
        return {m.group(1): m.group(2).strip() for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", block)}

    dark_re = re.compile(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{([^}]*)\}\s*\}")
    m = dark_re.search(css)
    if m:
        dark = tokens(m.group(1))
        css = css[:m.start()] + css[m.end():]
    m = re.search(r"(?m)^:root\s*\{([^}]*)\}", css)
    if m:
        light = tokens(m.group(1))
        css = css[:m.start()] + css[m.end():]
    return css, light, dark


def strip_comments(text: str, suffix: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    if suffix == ".js":
        text = re.sub(r"(?m)^\s*//.*$", "", text)
    if suffix == ".html":
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return text


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def node_eval(expr: str):
    """Evaluate `expr` in node after running board.js with a bare `window` (the pure helpers need no DOM)."""
    script = ("const vm=require('vm');const fs=require('fs');const ctx={window:{},console};vm.createContext(ctx);"
              f"vm.runInContext(fs.readFileSync({json.dumps(str(UI / 'board.js'))},'utf8'),ctx);"
              f"const B=ctx.window.KanbanBoard;process.stdout.write(JSON.stringify(({expr})));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


class TokenTests(unittest.TestCase):
    def test_tokens_and_no_raw_colours(self):
        for path in PRD05_FILES:
            text = strip_comments(read(path), path.suffix)
            if path.name == "board.css":
                text, light, dark = css_token_blocks(text)
                self.assertTrue(light and dark, "board.css needs a :root block and a dark :root block")
            if path.suffix == ".js":  # a location fragment (#t=) and CSS selectors are not colours
                text = text.replace("#t=", "")
            for pattern in (HEX, FUNC, NAMED):
                hit = pattern.search(text)
                self.assertIsNone(hit, f"colour literal {hit and hit.group(0)!r} outside the token blocks in {path.name}")

    def test_token_scale_and_contrast(self):
        _, light, dark = css_token_blocks(read(UI / "board.css"))
        for name, value in (("--space-1", "4px"), ("--space-2", "8px"), ("--space-3", "12px"), ("--space-4", "16px"),
                            ("--space-5", "24px"), ("--text-xs", "12px"), ("--text-sm", "13px"),
                            ("--text-md", "15px"), ("--text-lg", "18px")):
            self.assertEqual(light.get(name), value, name)
        for name in ("--radius", "--accent", "--ok", "--warn", "--bad", "--surface", "--bg", "--text", "--muted"):
            self.assertIn(name, light)
        texts = ("--text", "--muted", "--accent-text", "--ok", "--warn", "--bad")
        grounds = ("--bg", "--surface", "--col")
        for theme, tokens in (("light", light), ("dark", {**light, **dark})):
            for fg in texts:
                for bg in grounds:
                    ratio = contrast(tokens[fg], tokens[bg])
                    self.assertGreaterEqual(ratio, 4.5, f"{theme}: {fg} on {bg} = {ratio:.2f}")
            self.assertGreaterEqual(contrast(tokens["--on-accent"], tokens["--accent"]), 4.5, theme)

    def test_module_classes_exposed(self):
        css = read(UI / "board.css")
        for cls in (".panel", ".muted", ".term-bar"):
            self.assertRegex(css, re.escape(cls) + r"\b[^{]*\{", cls)


class PluginApiTests(unittest.TestCase):
    def test_tabs_api_backward_compatible(self):
        app = read(UI / "app.js")
        self.assertIn("addDrawerPanel(id, title, render, opts = {})", app)
        self.assertIn("B.resolveTab(opts.tab", app)
        board = read(UI / "board.js")
        self.assertIn(f"const TABS = {json.dumps(TABS)};", board)
        if not shutil.which("node"):
            self.skipTest("node not installed")
        got = node_eval("[B.resolveTab(undefined), B.resolveTab(''), B.resolveTab('bogus'), "
                        "B.resolveTab('terminal'), B.resolveTab('spec'), B.resolveTab('runs'), B.resolveTab('git'), "
                        "B.resolveTab('overview'), B.TABS]")
        self.assertEqual(got, ["overview", "overview", "overview", "terminal", "spec", "runs", "git", "overview", TABS])

    def test_project_panel_api(self):
        app = read(UI / "app.js")
        self.assertIn("addProjectPanel(id, title, render)", app)
        self.assertIn("window.kanban = {", app)
        self.assertNotRegex(app, r"Object\.(freeze|seal|preventExtensions)\(\s*window\.kanban")
        self.assertNotRegex(app, r"Object\.defineProperty\(\s*window\s*,\s*[\"']kanban")
        self.assertIn("This panel failed to load", app)
        self.assertIn('addProjectPanel("connection", "Connection"', app)

    def test_connect_feature_detection(self):
        app = read(UI / "app.js")
        self.assertIn('typeof window.kanban.connectClaude === "function"', app)
        body = re.search(r"function connectControls\(.*?\n  \}\n", app, re.S).group(0)
        self.assertIn('typeof window.kanban.connectClaude === "function"', body)  # decided at render time
        for caller in ("function renderConnection", "function renderWelcome"):  # panel and welcome share it
            fn = app[app.index(caller):]
            fn = fn[:fn.index("\n  }\n")]
            self.assertIn("connectControls(", fn, caller)
        self.assertIn("claude --dangerously-load-development-channels plugin:kanban@agentic-kit", app)

    def test_session_labels(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        got = node_eval("[['cli',true],['cli',false],['cli-print',false],['code-tab',false],['headless',false],"
                        "['unknown',false],['weird',false]].map(([o,c])=>B.sessionLabel({origin:o,channel:c}))")
        self.assertEqual(got, ["CLI + channels", "CLI", "CLI print", "Code tab", "headless", "unknown", "unknown"])
        got = node_eval("[B.sessionSummary([]), B.sessionSummary([{kind:'interactive',channel:true,origin:'cli'}]),"
                        "B.sessionSummary([{kind:'interactive',channel:false,origin:'code-tab'},"
                        "{kind:'headless',channel:false,origin:'headless'}])]")
        self.assertEqual(got, ["No sessions", "1 session · channels on", "2 sessions · channels off"])

    def test_rail_state_survives_broken_storage(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        got = node_eval("[B.loadRails({getItem(){throw new Error('private')}}), B.loadRails(null),"
                        "B.loadRails({getItem(){return '{\"done\":true,\"x\":1}'}}),"
                        "B.loadRails({getItem(){return 'not json'}}),"
                        "(()=>{try{B.saveRails({setItem(){throw new Error('quota')}},{done:true});return 'ok'}"
                        "catch(e){return 'threw'}})()]")
        self.assertEqual(got, [{}, {}, {"done": True}, {}, "ok"])
        self.assertIn('const RAILS = ["e2e", "done"];', read(UI / "board.js"))


class HeaderAndOnboardingTests(unittest.TestCase):
    def test_header_controls_present(self):
        html = read(UI / "index.html")
        header = re.search(r'<header class="top".*?</header>', html, re.S).group(0)
        for needle in ('id="project"', 'id="add-project"', ">Add project<", 'id="new-ticket-toggle"', ">New ticket<",
                       'id="new-ticket"', 'id="conn-chip"', 'id="help-toggle"', 'aria-haspopup="menu"'):
            self.assertIn(needle, header, needle)
        self.assertNotIn(".SDD/specs", html)  # developer-internal text lives in the help menu only (FR-10)
        app = read(UI / "app.js")
        self.assertIn("Show welcome", app)
        self.assertIn("Remove project", app)

    def test_welcome_uses_onboarding_state(self):
        app = read(UI / "app.js")
        self.assertIn('projectPath("/onboarding")', app)
        self.assertIn("{dismissed: true}", app)
        for event in ("session.changed", "project.removed", "project.registered", "board.changed"):
            self.assertIn(f'"{event}"', app, event)
        self.assertIn('plugin_python === "missing"', app)

    def test_add_project_uses_dry_run(self):
        app = read(UI / "app.js")
        self.assertIn('"/api/projects/add"', app)
        preview = app.index("dry_run: true")
        real = app.index("create_specs: ")
        self.assertLess(preview, real, "the preview call comes first")
        self.assertIn('invoke("pick_folder")', app)
        self.assertIn("is_home", app)
        self.assertIn("already_registered", app)
        self.assertIn('{method: "DELETE"}', app)

    def test_queued_card_explanation(self):
        runs = read(MODULES / "runs.js")
        self.assertIn("No Claude session with channels is connected to this project — Code-tab sessions don't "
                      "receive board moves", runs)
        self.assertIn('"Copy prompt"', runs)
        self.assertIn('"Run headless"', runs)
        self.assertIn("Code-tab sessions don't receive board moves", read(UI / "app.js"))  # connection panel

    def test_card_menu_and_tabs_keyboard(self):
        board = read(UI / "board.js")
        self.assertRegex(board, r'"aria-haspopup"[:,] "menu"')
        for needle in ('role: "menu"', 'role: "menuitem"', '"ArrowDown"', '"Escape"'):
            self.assertIn(needle, board, needle)
        app = read(UI / "app.js")
        self.assertIn('role="tablist"', read(UI / "index.html"))
        for needle in ('role: "tab"', '"aria-selected"', '"Home"', '"End"', '"ArrowRight"'):
            self.assertIn(needle, app, needle)


class ServedUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = helpers.TestDaemon().start()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def test_no_inline_script_or_new_html_sinks(self):
        status, headers, html = helpers.request(self.d.port, "GET", "/")
        self.assertEqual(status, 200)
        html = html.decode()
        self.assertIn("script-src 'self'", headers["content-security-policy"])
        for tag in re.findall(r"<script\b[^>]*>", html):
            self.assertIn("src=", tag)
        self.assertIsNone(re.search(r"<[a-zA-Z][^>]*\son[a-z]+\s*=", html))
        listing = self.d.call("GET", "/ui/modules", token=None)[1]
        sinks = 0
        for path in ["/ui/app.js", "/ui/board.js"] + listing["js"]:
            status, _, js = helpers.request(self.d.port, "GET", path)
            self.assertEqual(status, 200, path)
            js = js.decode()
            sinks += len(re.findall(r"\.innerHTML\s*=", js))
            for bad in ("insertAdjacentHTML", "outerHTML", "document.write", "DOMParser", "createContextualFragment",
                        "setAttribute(\"on", "new Function"):
                self.assertNotIn(bad, js, f"{bad} in {path}")
        self.assertEqual(sinks, 1, "only the /view fragment in app.js may use innerHTML")
        app = helpers.request(self.d.port, "GET", "/ui/app.js")[2].decode()
        self.assertEqual(len(re.findall(r"\.innerHTML\s*=", app)), 1)


if __name__ == "__main__":
    unittest.main()
