#!/usr/bin/env python3
"""Tests for the bundled Python runtime (PRD-01, ADR-001): app/python.lock, app/scripts/fetch-python.sh and the
release-only Tauri config.

Every fetch runs against a tiny local archive built here from tests/fixtures/fake_python (a fake `bin/python3` that
answers --version and hands `-m compileall` to this interpreter) through a `file://` URL in a test lock. Nothing is
downloaded from the network.

Run: python3 -m unittest tests/test_app_python_lock.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

KIT = Path(__file__).resolve().parent.parent
APP = KIT / "plugins" / "kanban" / "app"
LOCK = APP / "python.lock"
FETCH = APP / "scripts" / "fetch-python.sh"
FIXTURE = KIT / "tests" / "fixtures" / "fake_python" / "python"
STDLIB = "lib/python3.12"
LICENCE = f"{STDLIB}/LICENSE.txt"

# Paths (relative to the unpacked tree) that must be gone after a fetch (PRD-01 AC, ADR-001, Q13).
PRUNED = [
    f"{STDLIB}/test", f"{STDLIB}/json/tests", f"{STDLIB}/idlelib", f"{STDLIB}/tkinter", f"{STDLIB}/turtledemo",
    f"{STDLIB}/ensurepip", f"{STDLIB}/lib2to3", f"{STDLIB}/pydoc_data", "lib/tcl8.6", "lib/tk8.6",
    "lib/libtcl8.6.dylib", "lib/libtk8.6.dylib", "include", f"{STDLIB}/config-3.12-darwin", "lib/libpython3.12.a",
    f"{STDLIB}/lib-dynload/_tkinter.cpython-312-darwin.so", f"{STDLIB}/__pycache__/stale.cpython-312.pyc",
    # the real 20260924 archive (measured 2026-09-29): Tcl/Tk 9.0, pip, share/, pkgconfig, dev launchers, and a
    # libpython dylib the statically linked interpreter never loads
    "lib/tcl9.0", "lib/tk9.0", "lib/libtcl9.0.dylib", "lib/libtcl9tk9.0.dylib", "lib/libpython3.12.dylib",
    "lib/pkgconfig", "share", f"{STDLIB}/site-packages/pip", f"{STDLIB}/site-packages/pip-26.2.1.dist-info",
    "bin/pip", "bin/pip3", "bin/idle3.12", "bin/2to3-3.12", "bin/pydoc3.12", "bin/python3.12-config",
    "bin/python", "bin/python3.12",
]
KEPT = [
    "bin/python3", f"{STDLIB}/os.py", f"{STDLIB}/json/__init__.py",
    f"{STDLIB}/json/decoder.py", f"{STDLIB}/lib-dynload/_json.cpython-312-darwin.so", LICENCE,
]


def build_archive(where: Path) -> tuple[Path, str]:
    """An install_only_stripped-shaped .tar.gz (top-level `python/`) plus a shipped __pycache__ entry."""
    src = where / "src" / "python"
    shutil.copytree(FIXTURE, src, symlinks=True)
    (src / STDLIB / "__pycache__").mkdir()
    (src / STDLIB / "__pycache__" / "stale.cpython-312.pyc").write_bytes(b"\x00stale bytecode shipped upstream")
    archive = where / "cpython-3.12.14+fake-install_only_stripped.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(src, arcname="python")
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def write_lock(where: Path, url: str, sha256: str, *, max_mb: int = 45, licences=None, arches=("arm64", "x86_64")):
    triples = {"arm64": "aarch64-apple-darwin", "x86_64": "x86_64-apple-darwin"}
    lock = {
        "version": "3.12.14",
        "build_date": "fake",
        "flavor": "install_only_stripped",
        "max_unpacked_mb": max_mb,
        "licence_files": licences if licences is not None else [LICENCE],
        "arches": {a: {"triple": triples[a], "url": url, "sha256": sha256} for a in arches},
    }
    path = where / "test.lock"
    path.write_text(json.dumps(lock, indent=2))
    return path


def fetch(*args, env_extra=None):
    env = dict(os.environ, FAKE_PYTHON_HOST=sys.executable)
    env.update(env_extra or {})
    return subprocess.run(["bash", str(FETCH), *[str(a) for a in args]], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=120, env=env)


class LockFileTests(unittest.TestCase):
    def test_lock_format(self):
        lock = json.loads(LOCK.read_text())
        self.assertTrue(lock["version"].startswith("3.12."), lock["version"])
        self.assertRegex(lock["build_date"], r"^\d{8}$")
        self.assertEqual(lock["flavor"], "install_only_stripped")
        self.assertEqual(lock["max_unpacked_mb"], 45)  # changes only with an ADR-001 note
        self.assertTrue(lock["licence_files"])
        for rel in lock["licence_files"]:
            self.assertFalse(rel.startswith("/") or ".." in Path(rel).parts, rel)
        self.assertEqual(set(lock["arches"]), {"arm64", "x86_64"})
        triples = {"arm64": "aarch64-apple-darwin", "x86_64": "x86_64-apple-darwin"}
        for arch, entry in lock["arches"].items():
            url = urlparse(entry["url"])
            self.assertEqual(url.scheme, "https", arch)
            self.assertEqual(url.netloc, "github.com", arch)
            self.assertTrue(url.path.startswith(
                f"/astral-sh/python-build-standalone/releases/download/{lock['build_date']}/"), url.path)
            self.assertIn(triples[arch], url.path)
            self.assertIn("install_only_stripped", url.path)
            self.assertIn(lock["version"], url.path.replace("%2B", "+"))
            self.assertEqual(entry["triple"], triples[arch])
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$", arch)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.archive, self.sum = build_archive(self.t)
        self.url = self.archive.as_uri()
        self.dest = self.t / "dest"

    def tearDown(self):
        self.tmp.cleanup()

    def good_fetch(self, *args, **lock_kw):
        lock = write_lock(self.t, self.url, self.sum, **lock_kw)
        res = fetch("--arch", "arm64", "--lock", lock, "--dest", self.dest, *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res

    def test_fetch_refuses_bad_sum(self):
        lock = write_lock(self.t, self.url, "0" * 64)
        res = fetch("--arch", "arm64", "--lock", lock, "--dest", self.dest)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("sha256", res.stderr)
        self.assertFalse(self.dest.exists())

    def test_prune_list_and_licences_kept(self):
        self.good_fetch()
        tree = self.dest / "arm64"
        for rel in PRUNED:
            self.assertFalse((tree / rel).exists(), rel)
        for rel in KEPT:
            self.assertTrue((tree / rel).exists(), rel)
        self.assertTrue(os.access(tree / "bin" / "python3", os.X_OK))
        # one real interpreter file: Tauri copies symlinks as full files, which would triple the binary
        self.assertFalse((tree / "bin" / "python3").is_symlink())
        self.assertEqual(sorted(p.name for p in (tree / "bin").iterdir()), ["python3"])

    def test_precompiled_unchecked_hash(self):
        self.good_fetch()
        stdlib = self.dest / "arm64" / STDLIB
        sources = sorted(stdlib.rglob("*.py"))
        self.assertEqual(len(sources), 3, sources)  # os.py, json/__init__.py, json/decoder.py
        for py in sources:
            pycs = list((py.parent / "__pycache__").glob(f"{py.stem}.*.pyc"))
            self.assertEqual(len(pycs), 1, py)
            flags = int.from_bytes(pycs[0].read_bytes()[4:8], "little")
            self.assertEqual(flags, 0b01, pycs[0])  # PEP 552: hash-based, check_source off = unchecked-hash

    def test_size_ceiling(self):
        lock = write_lock(self.t, self.url, self.sum, max_mb=0)
        res = fetch("--arch", "arm64", "--lock", lock, "--dest", self.dest)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertRegex(res.stderr, r"\d+\.\d MB.*ceiling 0 MB")
        self.assertFalse(self.dest.exists())

    def test_size_printed_on_success(self):
        res = self.good_fetch()
        self.assertRegex(res.stdout, r"arm64: unpacked size \d+\.\d MB \(ceiling 45 MB\)")

    def test_unknown_arch_exits_2_with_usage(self):
        res = fetch("--arch", "ppc", "--lock", write_lock(self.t, self.url, self.sum), "--dest", self.dest)
        self.assertEqual(res.returncode, 2)
        self.assertIn("usage:", res.stderr)
        self.assertFalse(self.dest.exists())

    def test_download_failure_leaves_dest_untouched(self):
        old = self.dest / "arm64"
        old.mkdir(parents=True)
        (old / "marker").write_text("older lock")
        lock = write_lock(self.t, (self.t / "missing.tar.gz").as_uri(), self.sum)
        res = fetch("--arch", "arm64", "--lock", lock, "--dest", self.dest)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["arm64"])
        self.assertEqual((old / "marker").read_text(), "older lock")

    def test_existing_tree_replaced_only_after_checks(self):
        old = self.dest / "arm64"
        old.mkdir(parents=True)
        (old / "marker").write_text("older lock")
        bad = write_lock(self.t, self.url, self.sum, max_mb=0)
        self.assertEqual(fetch("--arch", "arm64", "--lock", bad, "--dest", self.dest).returncode, 1)
        self.assertTrue((old / "marker").exists())
        self.good_fetch()
        self.assertFalse((old / "marker").exists())
        self.assertTrue((old / "bin" / "python3").exists())
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["arm64"])

    def test_missing_licence_file_fails(self):
        lock = write_lock(self.t, self.url, self.sum, licences=[LICENCE, "lib/tcl8.6/license.terms"])
        res = fetch("--arch", "arm64", "--lock", lock, "--dest", self.dest)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("lib/tcl8.6/license.terms", res.stderr)
        self.assertFalse(self.dest.exists())

    def test_compileall_failure_fails(self):
        lock = write_lock(self.t, self.url, self.sum)
        res = fetch("--arch", "arm64", "--lock", lock, "--dest", self.dest, env_extra={"FAKE_PYTHON_FAIL_COMPILE": "1"})
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertFalse(self.dest.exists())

    def test_non_https_url_refused(self):
        lock = write_lock(self.t, "http://github.com/astral-sh/python-build-standalone/x.tar.gz", self.sum)
        res = fetch("--arch", "arm64", "--lock", lock, "--dest", self.dest)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("https", res.stderr)
        self.assertFalse(self.dest.exists())

    def test_both_arches(self):
        lock = write_lock(self.t, self.url, self.sum)
        res = fetch("--arch", "both", "--lock", lock, "--dest", self.dest)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        for arch in ("arm64", "x86_64"):
            self.assertTrue((self.dest / arch / "bin" / "python3").exists(), arch)
            self.assertTrue(list((self.dest / arch / STDLIB / "__pycache__").glob("os.*.pyc")), arch)
            self.assertFalse((self.dest / arch / "include").exists(), arch)


class ConfigTests(unittest.TestCase):
    def test_default_config_has_no_python(self):
        default = json.loads((APP / "src-tauri" / "tauri.conf.json").read_text())
        self.assertNotIn("python", json.dumps(default["bundle"]["resources"]))
        release = json.loads((APP / "src-tauri" / "tauri.release.conf.json").read_text())
        resources = release["bundle"]["resources"]
        # Directory entries (not globs): Tauri flattens globbed files into the target folder, a directory keeps its tree.
        self.assertEqual(resources, {"resources/python/arm64": "python/arm64",
                                     "resources/python/x86_64": "python/x86_64"})

    def test_fetched_runtimes_are_ignored(self):
        lines = (APP / ".gitignore").read_text().splitlines()
        self.assertIn("src-tauri/resources/python/", lines)


if __name__ == "__main__":
    unittest.main()
