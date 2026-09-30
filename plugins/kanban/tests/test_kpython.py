#!/usr/bin/env python3
"""scripts/kpython (PRD-03 step 2, ADR-001, Q12): the plugin's POSIX-sh launcher picks Kanban.app's bundled Python
(~/Applications, then /Applications), then `python3` on PATH (>= 3.9; /usr/bin/python3 only with the CLT), run-checks
bundled candidates, isolates a bundled interpreter with -E -B and gives a system one PYTHONDONTWRITEBYTECODE=1.
Every case runs with a fake HOME, a fake system Applications folder (KANBAN_APPLICATIONS_DIR) and a controlled PATH;
the "pythons" are small sh wrappers around this interpreter (or scripts that exit 1). Also pins the daemon's
plugin_python hint to the same order (parity). Stdlib only."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers
import daemon as kd

KPYTHON = helpers.SCRIPTS / "kpython"
REAL = sys.executable
ARCH = "arm64" if platform.machine() in ("arm64", "aarch64") else platform.machine()
BUNDLE = f"Kanban.app/Contents/Resources/python/{ARCH}/bin/python3"
REPORT = ("import json, os, sys\n"
          "print(json.dumps({'which': os.environ.get('KPY_WHICH'), 'E': sys.flags.ignore_environment,\n"
          "                  'B': sys.flags.dont_write_bytecode, 'env_B': os.environ.get('PYTHONDONTWRITEBYTECODE'),\n"
          "                  'argv': sys.argv[1:]}))\n")
MAC = sys.platform == "darwin"


def write_exe(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class KpythonCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kanban-kpython-"))
        self.home = self.tmp / "home"
        self.apps = self.tmp / "Applications"  # stands in for /Applications
        self.bin = self.tmp / "bin"
        for d in (self.home, self.apps, self.bin):
            d.mkdir()
        os.symlink("/usr/bin/uname", self.bin / "uname")
        self.report = self.tmp / "report.py"
        self.report.write_text(REPORT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- fixtures
    def bundled(self, where: str, ok: bool = True) -> Path:
        base = self.home / "Applications" if where == "home" else self.apps
        body = f'KPY_WHICH={where}-bundled exec "{REAL}" "$@"\n' if ok else "exit 1\n"
        return write_exe(base / BUNDLE, "#!/bin/sh\n" + body)

    def system_python(self, folder: Path | None = None, ok: bool = True) -> Path:
        folder = folder or self.bin
        body = f'KPY_WHICH=system exec "{REAL}" "$@"\n' if ok else "exit 1\n"
        return write_exe(folder / "python3", "#!/bin/sh\n" + body)

    def env(self, path: str | None = None, **extra) -> dict:
        env = {"HOME": str(self.home), "PATH": path if path is not None else str(self.bin),
               "KANBAN_APPLICATIONS_DIR": str(self.apps)}
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def kpy(self, *args, env: dict | None = None):
        return subprocess.run(["/bin/sh", str(KPYTHON), *args], capture_output=True, text=True, timeout=60,
                              env=env or self.env())

    def launched(self, env: dict | None = None) -> dict:
        out = self.kpy(str(self.report), "a", "b c", env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def probe(self, env: dict | None = None) -> tuple:
        out = self.kpy("--probe", env=env)
        return out.returncode, out.stdout, out.stderr


@unittest.skipUnless(MAC, "the bundled candidates exist on macOS only")
class KpythonTests(KpythonCase):
    def test_kpython_order(self):
        home_py, apps_py = self.bundled("home"), self.bundled("system")
        # a python3 on PATH that fails the version check is skipped for the next one
        old = self.tmp / "old"
        self.system_python(old, ok=False)
        sys_py = self.system_python()
        path = f"{old}:{self.bin}"
        self.assertEqual(self.probe(self.env(path)), (0, f"bundled {home_py}\n", ""))
        self.assertEqual(self.launched(self.env(path))["which"], "home-bundled")
        home_py.unlink()
        self.assertEqual(self.probe(self.env(path)), (0, f"bundled {apps_py}\n", ""))
        self.assertEqual(self.launched(self.env(path))["which"], "system-bundled")
        apps_py.unlink()
        self.assertEqual(self.probe(self.env(path)), (0, f"system {sys_py}\n", ""))
        self.assertEqual(self.launched(self.env(path))["which"], "system")

    def test_kpython_missing(self):
        out = self.kpy(str(self.report))
        self.assertEqual((out.returncode, out.stdout), (127, ""))
        lines = out.stderr.splitlines()
        self.assertEqual(len(lines), 1, out.stderr)
        self.assertIn("Kanban.app", lines[0])  # fix 1: move the app to Applications
        self.assertIn("python3", lines[0])  # fix 2: install python3
        quiet = self.kpy("--quiet-missing", str(self.report))
        self.assertEqual((quiet.returncode, quiet.stdout, quiet.stderr), (0, "", ""))
        self.assertEqual(self.probe(), (3, "missing\n", ""))

    def test_kpython_probe(self):
        self.system_python()
        self.assertEqual(self.probe(), (0, f"system {self.bin / 'python3'}\n", ""))
        home_py = self.bundled("home")
        self.assertEqual(self.probe(), (0, f"bundled {home_py}\n", ""))
        # --quiet-missing only changes the missing case
        out = self.kpy("--quiet-missing", str(self.report), "x")
        self.assertEqual((out.returncode, json.loads(out.stdout)["argv"]), (0, ["x"]))

    def test_kpython_env(self):
        self.bundled("home")
        got = self.launched(self.env(PYTHONDONTWRITEBYTECODE=""))
        self.assertEqual((got["which"], got["E"], got["B"], got["argv"]), ("home-bundled", 1, 1, ["a", "b c"]))
        (self.home / "Applications").rename(self.tmp / "gone")
        self.system_python()
        got = self.launched()
        self.assertEqual((got["which"], got["E"], got["B"], got["env_B"], got["argv"]),
                         ("system", 0, 1, "1", ["a", "b c"]))

    def test_kpython_broken_bundled_falls_back(self):
        broken = self.bundled("home", ok=False)
        sys_py = self.system_python()
        code, out, err = self.probe()
        self.assertEqual((code, out), (0, f"system {sys_py}\n"))
        self.assertEqual(err, f"bundled {broken} failed\n")
        self.assertEqual(self.launched()["which"], "system")
        good = self.bundled("system")  # /Applications is still tried after a broken ~/Applications
        self.assertEqual(self.probe()[:2], (0, f"bundled {good}\n"))

    def test_kpython_ignores_pythonhome(self):
        bogus = self.env(PYTHONHOME="/bogus", PYTHONPATH="/bogus")
        # sanity: this interpreter does not start with that PYTHONHOME unless -E is given
        plain = subprocess.run([REAL, "-c", "import sys"], capture_output=True, env=bogus, timeout=30)
        self.assertNotEqual(plain.returncode, 0)
        self.bundled("home")
        got = self.launched(bogus)
        self.assertEqual((got["which"], got["E"]), ("home-bundled", 1))
        self.assertEqual(self.probe(bogus)[0], 0)

    @unittest.skipUnless(os.access("/usr/bin/python3", os.X_OK), "no /usr/bin/python3 on this machine")
    def test_kpython_usr_bin_stub_needs_clt(self):
        write_exe(self.bin / "xcode-select", "#!/bin/sh\nexit 2\n")  # no Command Line Tools
        path = f"{self.bin}:/usr/bin"
        self.assertEqual(self.probe(self.env(path)), (3, "missing\n", ""))
        write_exe(self.bin / "xcode-select", "#!/bin/sh\necho /Library/Developer/CommandLineTools\n")
        code, out, _ = self.probe(self.env(path))
        self.assertEqual((code, out), (0, "system /usr/bin/python3\n"))

    def test_kpython_runs_without_exec_bit(self):
        self.assertTrue(KPYTHON.is_file())
        self.assertTrue(KPYTHON.read_text().startswith("#!/bin/sh\n"))
        self.system_python()
        copy = self.tmp / "kpython"
        shutil.copy(KPYTHON, copy)
        copy.chmod(0o644)  # plugin caches may drop the exec bit: it is always run through sh
        out = subprocess.run(["/bin/sh", str(copy), str(self.report)], capture_output=True, text=True, timeout=60,
                             env=self.env())
        self.assertEqual((out.returncode, json.loads(out.stdout)["which"]), (0, "system"))


class EntryPointTests(unittest.TestCase):
    def test_mcp_json_uses_the_launcher(self):
        spec = json.loads((helpers.PLUGIN / ".mcp.json").read_text())["mcpServers"]["kanban"]
        self.assertEqual((spec["command"], spec["args"]),
                         ("sh", ["${CLAUDE_PLUGIN_ROOT}/scripts/kpython", "${CLAUDE_PLUGIN_ROOT}/scripts/server.py"]))


@unittest.skipUnless(MAC, "the bundled candidates exist on macOS only")
class PluginPythonParityTests(KpythonCase):
    def hint(self, path: str | None = None) -> str:
        return kd.plugin_python_hint(home=self.home, path=path if path is not None else str(self.bin),
                                     apps=self.apps, fresh=True)

    def probe_kind(self, path: str | None = None) -> str:
        return self.probe(self.env(path))[1].split()[0]

    def test_plugin_python_parity(self):
        scenarios = []
        self.assertEqual((self.hint(), self.probe_kind()), ("missing", "missing"))
        scenarios.append("missing")
        self.system_python()
        self.assertEqual((self.hint(), self.probe_kind()), ("system", "system"))
        self.bundled("home", ok=False)
        self.assertEqual((self.hint(), self.probe_kind()), ("system", "system"))  # broken bundle skipped
        self.bundled("system")
        self.assertEqual((self.hint(), self.probe_kind()), ("bundled", "bundled"))
        self.bundled("home")
        self.assertEqual((self.hint(), self.probe_kind()), ("bundled", "bundled"))
        if os.access("/usr/bin/python3", os.X_OK):
            shutil.rmtree(self.home / "Applications")
            shutil.rmtree(self.apps / "Kanban.app")
            (self.bin / "python3").unlink()
            write_exe(self.bin / "xcode-select", "#!/bin/sh\nexit 2\n")
            path = f"{self.bin}:/usr/bin"
            self.assertEqual((self.hint(path), self.probe_kind(path)), ("missing", "missing"))

    def test_hint_is_cached(self):
        self.system_python()
        self.assertEqual(self.hint(), "system")
        (self.bin / "python3").unlink()
        cached = kd.plugin_python_hint(home=self.home, path=str(self.bin), apps=self.apps)
        self.assertEqual(cached, "system")  # within 30 s the cached answer is reused
        self.assertEqual(self.hint(), "missing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
