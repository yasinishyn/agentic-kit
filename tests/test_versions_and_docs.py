#!/usr/bin/env python3
"""Tests for the v0.3.1 release docs and version fields (PRD-07, KB31-FR-11, 15; Q13, Q14).

- the five version fields (and the Cargo.lock root package) agree, as the release workflow's tag check requires;
- every image and relative link in the root and plugin READMEs resolves, and in-page anchors exist;
- LICENSES/NOTICE.md names every licence file that python.lock keeps in the bundled runtime;
- the plugin README carries the Gatekeeper steps, the troubleshooting rows and the maintainer release steps, and the
  app's "Troubleshooting" link (ui/modules/terminal.js HELP_URL) points at a heading that exists.

Run: python3 -m unittest tests/test_versions_and_docs.py
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
PLUGIN = KIT / "plugins" / "kanban"
TAURI = PLUGIN / "app" / "src-tauri"
READMES = [KIT / "README.md", PLUGIN / "README.md"]
NOTICE = KIT / "LICENSES" / "NOTICE.md"
PYTHON_LOCK = PLUGIN / "app" / "python.lock"
TERMINAL_JS = PLUGIN / "ui" / "modules" / "terminal.js"
EXPECTED_VERSION = "0.3.1"

LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_IMG = re.compile(r"<img\s[^>]*src=\"([^\"]+)\"")
FENCE = re.compile(r"^```.*?^```", re.M | re.S)


def grab(path: Path, pattern: str) -> str | None:
    m = re.search(pattern, path.read_text(), re.M)
    return m.group(1) if m else None


def versions() -> dict:
    market = json.loads((KIT / ".claude-plugin" / "marketplace.json").read_text())
    lock = (TAURI / "Cargo.lock").read_text()
    root = re.search(r'\[\[package\]\]\nname = "kanban-app"\nversion = "([^"]+)"', lock)
    return {
        "plugin.json": json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text()).get("version"),
        "marketplace.json": next((p.get("version") for p in market["plugins"] if p.get("name") == "kanban"), None),
        "daemon.py VERSION": grab(PLUGIN / "scripts" / "daemon.py", r'^VERSION = .*"([^"]+)"\s*$'),
        "tauri.conf.json": json.loads((TAURI / "tauri.conf.json").read_text()).get("version"),
        "Cargo.toml": grab(TAURI / "Cargo.toml", r'^version = "([^"]+)"'),
        "Cargo.lock root": root.group(1) if root else None,
    }


def slug(heading: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped (word characters, spaces and hyphens kept),
    spaces to hyphens."""
    text = re.sub(r"[`*]", "", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(md: str) -> set:
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", FENCE.sub("", md), re.M)}


def links(md: str) -> list:
    body = FENCE.sub("", md)
    return LINK.findall(body) + HTML_IMG.findall(body)


class VersionTests(unittest.TestCase):
    def test_versions_match(self):
        found = versions()
        self.assertEqual(found, {k: EXPECTED_VERSION for k in found})


class ReadmeLinkTests(unittest.TestCase):
    def test_readme_links_resolve(self):
        for readme in READMES:
            md = readme.read_text()
            own = anchors(md)
            for target in links(md):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # http(s), mailto, …
                    continue
                path, _, frag = target.partition("#")
                with self.subTest(readme=readme.relative_to(KIT).as_posix(), link=target):
                    if not path:
                        self.assertIn(frag, own, "in-page anchor has no heading")
                        continue
                    resolved = (readme.parent / path).resolve()
                    self.assertTrue(resolved.exists(), f"{resolved} does not exist")
                    if frag and resolved.suffix == ".md":
                        self.assertIn(frag, anchors(resolved.read_text()), "anchor has no heading")

    def test_plugin_readme_shows_png_screenshots(self):
        md = (PLUGIN / "README.md").read_text()
        images = [t for t in links(md) if t.startswith("docs/img/")]
        self.assertGreaterEqual(len(images), 5, images)
        for image in images:
            with self.subTest(image=image):
                self.assertTrue(image.endswith(".png"))
                data = (PLUGIN / image).read_bytes()
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", "not a PNG file")

    def test_help_url_points_at_troubleshooting(self):
        url = grab(TERMINAL_JS, r'^\s*const HELP_URL = "([^"]+)";')
        self.assertEqual(url, "https://github.com/yasinishyn/agentic-kit/blob/main/plugins/kanban/README.md"
                              "#troubleshooting")
        self.assertIn("troubleshooting", anchors((PLUGIN / "README.md").read_text()))


class NoticeTests(unittest.TestCase):
    def test_notice_lists_runtime_licences(self):
        lock = json.loads(PYTHON_LOCK.read_text())
        notice = NOTICE.read_text()
        self.assertIn("python-build-standalone", notice)
        self.assertIn("Python Software Foundation", notice)
        self.assertIn(lock["version"], notice)
        self.assertIn(lock["build_date"], notice)
        self.assertTrue(lock["licence_files"])
        for arch in lock["arches"]:
            for rel in lock["licence_files"]:
                with self.subTest(arch=arch, licence=rel):
                    self.assertIn(f"Kanban.app/Contents/Resources/python/{arch}/{rel}", notice)
        self.assertIn("not copied into", notice)
        # Q18: each statically linked component with its version, SPDX id and in-app licence file, one table row
        components = [
            ("OpenSSL", "3.5.8", "Apache-2.0", "openssl-3.5.8.txt"),
            ("SQLite", "3.53.1", "blessing", "sqlite-3.53.1.txt"),
            ("libffi", "3.4.8", "MIT", "libffi-3.4.8.txt"),
            ("bzip2", "1.0.8", "bzip2-1.0.6", "bzip2-1.0.8.txt"),
            ("XZ Utils (liblzma)", "5.8.4", "0BSD", "xz-5.8.4.txt"),
            ("mpdecimal", "4.0.0", "BSD-2-Clause", "mpdecimal-4.0.0.txt"),
            ("Expat", "2.8.5", "MIT", "expat-2.8.5.txt"),
            ("libuuid", "1.0.3", "BSD-3-Clause", "libuuid-1.0.3.txt"),
        ]
        rows = [line for line in notice.splitlines() if line.startswith("| ")]
        for name, version, spdx, file in components:
            with self.subTest(component=name):
                self.assertIn(f"licences/{file}", lock["licence_files"])
                row = [r for r in rows if f"python/arm64/licences/{file}" in r]
                self.assertEqual(len(row), 1, file)
                for text in (name, f"| {version} |", f"`{spdx}`", f"python/x86_64/licences/{file}"):
                    self.assertIn(text, row[0])
        self.assertIn("plugins/kanban/app/licences/python-runtime/SOURCES.md", notice)
        self.assertIn("gregoryszorc.com/docs/python-build-standalone", notice)  # secondary reference


class PluginReadmeContentTests(unittest.TestCase):
    def setUp(self):
        self.md = (PLUGIN / "README.md").read_text()

    def section(self, title: str) -> str:
        m = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", self.md, re.M | re.S)
        self.assertIsNotNone(m, f"no '## {title}' section")
        return m.group(1)

    def test_readme_has_gatekeeper_and_troubleshooting(self):
        for text in ("Open Anyway", "Privacy & Security", "xattr -dr com.apple.quarantine /Applications/Kanban.app",
                     "SHA256SUMS", "right-click"):
            self.assertIn(text, self.md)
        rows = self.section("Troubleshooting")
        for text in ("kpython --probe", "login shell", "port", "channel", "Code tab", "Gatekeeper",
                     "approved before v0.3", "database is locked", "daemon.py --open --project", "sessions.origin",
                     "410"):
            with self.subTest(row=text):
                self.assertIn(text, rows)

    def test_readme_quick_start_has_three_routes(self):
        quick = self.section("Quick start")
        for text in ("get.sh | bash -s --", "Releases", "shasum -a 256", "--only kanban-app --from-source",
                     "cargo install tauri-cli --version '^2' --locked", "xcode-select --install", "sh.rustup.rs",
                     "fetch-python.sh --arch both"):
            with self.subTest(text=text):
                self.assertIn(text, quick)

    def test_readme_releasing_section(self):
        rel = self.section("Releasing (maintainers)")
        for text in ("kanban-v", "workflow_dispatch", "releases.lock", "SHA256SUMS", "draft", "Cargo.lock",
                     "APPLE_CERTIFICATE", "APPLE_SIGNING_IDENTITY"):
            with self.subTest(text=text):
                self.assertIn(text, rel)


if __name__ == "__main__":
    unittest.main()
