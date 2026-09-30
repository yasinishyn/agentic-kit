#!/usr/bin/env python3
"""Tests for the Kanban app release pipeline (PRD-04, ADR-002): .github/workflows/kanban-app-release.yml,
plugins/kanban/app/scripts/package-release.sh, the release entitlements and the release Tauri config.

The workflow is parsed with PyYAML when it is installed, else with the system Ruby's YAML (Psych, shipped with macOS
and GitHub's macOS runners); without either the workflow tests are skipped. Nothing here pushes, tags or calls GitHub.

The real packaging run (`test_package_host_only`) builds the app (~2 min, 84 MB) and runs only when
KANBAN_TEST_PACKAGE=1, `cargo tauri` exists and the bundled runtimes were fetched.

Run: python3 -m unittest tests/test_release_workflow.py
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
WORKFLOW = KIT / ".github" / "workflows" / "kanban-app-release.yml"
APP = KIT / "plugins" / "kanban" / "app"
PACKAGE = APP / "scripts" / "package-release.sh"
ENTITLEMENTS = APP / "src-tauri" / "entitlements.plist"
RELEASE_CONF = APP / "src-tauri" / "tauri.release.conf.json"
SHA = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
VERSION_FILES = [
    "plugins/kanban/.claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "plugins/kanban/scripts/daemon.py",
    "plugins/kanban/app/src-tauri/tauri.conf.json",
    "plugins/kanban/app/src-tauri/Cargo.toml",
]
ASSETS = ["Kanban.dmg", "Kanban.app.zip", "SHA256SUMS"]


def load_yaml(path: Path):
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text())
    except ImportError:
        pass
    ruby = shutil.which("ruby")
    if not ruby:
        raise unittest.SkipTest("neither PyYAML nor ruby is available to parse the workflow")
    out = subprocess.run([ruby, "-ryaml", "-rjson", "-e", "puts JSON.dump(YAML.load_file(ARGV[0]))", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def triggers(wf: dict) -> dict:
    # YAML 1.1 parsers read the bare key `on` as the boolean true.
    for key in ("on", True, "true"):
        if key in wf:
            return wf[key]
    raise AssertionError("workflow has no `on:` block")


def all_steps(wf: dict):
    for job_name, job in wf["jobs"].items():
        for step in job.get("steps", []):
            yield job_name, step


def walk(node, path=()):
    """Yield (path, key, value) for every mapping entry in the tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, k, v
            yield from walk(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, path + (i,))


class WorkflowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text()
        cls.wf = load_yaml(WORKFLOW)
        cls.build = cls.wf["jobs"]["build"]
        cls.release = cls.wf["jobs"]["release"]

    def step_index(self, job: dict, needle: str) -> int:
        for i, step in enumerate(job["steps"]):
            if needle in (step.get("run") or "") or needle in (step.get("uses") or ""):
                return i
        self.fail(f"no step contains {needle!r}")

    def step(self, job: dict, needle: str) -> dict:
        return job["steps"][self.step_index(job, needle)]


class WorkflowTests(WorkflowTestCase):
    def test_triggers_and_permissions(self):
        on = triggers(self.wf)
        self.assertEqual(set(on), {"push", "workflow_dispatch"})
        self.assertEqual(on["push"], {"tags": ["kanban-v*"]})
        self.assertFalse(on["workflow_dispatch"])  # no inputs: dispatch is always a dry run
        self.assertNotIn("pull_request", self.text)
        self.assertEqual(self.wf["permissions"], {"contents": "read"})

    def test_jobs_split(self):
        self.assertEqual(set(self.wf["jobs"]), {"build", "release"})
        self.assertEqual(self.build["runs-on"], "macos-14")
        self.assertEqual(self.build["permissions"], {"contents": "read"})
        checkout = self.step(self.build, "actions/checkout@")
        self.assertIs(checkout["with"]["persist-credentials"], False)

        self.assertEqual(self.release["permissions"], {"contents": "write"})
        self.assertEqual(self.release["needs"], "build")
        self.assertEqual(self.release["if"], "github.event_name == 'push'")
        for step in self.release["steps"]:
            self.assertNotIn("actions/checkout", step.get("uses", ""))
        self.step(self.release, "actions/download-artifact@")
        create = self.step(self.release, "gh release create")
        self.assertEqual(create["env"]["GH_TOKEN"], "${{ github.token }}")
        run = " ".join(create["run"].split())
        self.assertIn('gh release create "$GITHUB_REF_NAME" --repo "$GITHUB_REPOSITORY" --draft', run)
        self.assertIn('--title "$GITHUB_REF_NAME"', run)
        self.assertIn("--notes-file", run)
        self.assertIn("dist/Kanban.dmg dist/Kanban.app.zip dist/SHA256SUMS", run)
        self.assertEqual(re.findall(r"dist/[A-Za-z0-9.]+", run), [f"dist/{a}" for a in ASSETS])

    def test_release_notes_follow_signing(self):
        self.assertEqual(self.build["outputs"]["signed"], "${{ steps.signing.outputs.signed }}")
        notes = self.step(self.release, "unsigned")
        self.assertEqual(notes["env"]["SIGNED"], "${{ needs.build.outputs.signed }}")
        self.assertIn("unsigned — see first-launch steps", notes["run"])
        self.assertIn('[ "$SIGNED" = "1" ]', notes["run"])

    def test_dispatch_is_dry_run(self):
        # release: push only; signing: only when the event is a push; packaging: APPLE_* only when SIGNING=1
        self.assertEqual(self.release["if"], "github.event_name == 'push'")
        signing = self.wf["jobs"]["build"]["steps"][self.step_index(self.build, "SIGNING=1")]
        self.assertEqual(signing["env"]["EVENT_NAME"], "${{ github.event_name }}")
        self.assertIn('[ "$EVENT_NAME" = "push" ] && [ -n "$APPLE_CERTIFICATE" ]', signing["run"])
        package = self.step(self.build, "package-release.sh")
        apple = {k: v for k, v in package.get("env", {}).items() if k.startswith("APPLE_")}
        self.assertEqual(set(apple), {"APPLE_SIGNING_IDENTITY", "APPLE_ID", "APPLE_PASSWORD", "APPLE_TEAM_ID"})
        for key, value in apple.items():
            self.assertEqual(value, f"${{{{ env.SIGNING == '1' && secrets.{key} || '' }}}}")
        # and the script itself drops APPLE_* unless SIGNING=1
        script = PACKAGE.read_text()
        self.assertRegex(script, r'if \[ "\$\{SIGNING:-\}" != "1" \]; then\s+unset APPLE_SIGNING_IDENTITY APPLE_ID '
                                 r'APPLE_PASSWORD APPLE_TEAM_ID APPLE_CERTIFICATE APPLE_CERTIFICATE_PASSWORD')

    def test_actions_pinned_by_sha(self):
        uses = [(job, step["uses"]) for job, step in all_steps(self.wf) if "uses" in step]
        self.assertGreaterEqual(len(uses), 3)
        for job, ref in uses:
            self.assertRegex(ref, SHA, f"{job}: {ref}")
        for line in self.text.splitlines():
            if re.match(r"^\s*-?\s*uses:", line):
                self.assertRegex(line, r"@[0-9a-f]{40} # v\d+\.\d+\.\d+$", line)
        install = self.step(self.build, "cargo install tauri-cli")
        self.assertRegex(install["run"], r"cargo install tauri-cli --locked --version \d+\.\d+\.\d+")

    def test_signing_flag_step(self):
        for path, key, value in walk(self.wf):
            if key == "if":
                self.assertNotIn("secrets", str(value), f"secrets in if: at {path}")
        self.assertNotIn("secrets.", json.dumps(self.release))
        self.assertNotIn("secrets.", json.dumps({k: v for k, v in self.wf.items() if k != "jobs"}))
        signing = self.step(self.build, "SIGNING=1")
        self.assertEqual(signing["id"], "signing")
        self.assertIn('echo "SIGNING=1" >> "$GITHUB_ENV"', signing["run"])
        self.assertEqual(signing["env"]["APPLE_CERTIFICATE"], "${{ secrets.APPLE_CERTIFICATE }}")
        self.assertIn("security import", signing["run"])  # keychain import in the signing case
        self.assertIn("set -euo pipefail", signing["run"])  # a failed import fails the job

    def test_steps_order(self):
        idx = lambda n: self.step_index(self.build, n)  # noqa: E731
        tests = [idx("discover -s plugins/kanban/tests"), idx("discover -s tests"),
                 idx(".claude/hooks/test_guard_bash.sh"), idx("cargo test --locked")]
        self.assertLess(idx("rustup target add"), idx("cargo test --locked"))
        self.assertIn("aarch64-apple-darwin", self.step(self.build, "rustup target add")["run"])
        self.assertIn("x86_64-apple-darwin", self.step(self.build, "rustup target add")["run"])
        fetch = idx("fetch-python.sh --arch both")
        package = idx("package-release.sh")
        self.assertLess(max(tests), fetch)
        self.assertLess(idx("cargo install tauri-cli"), package)
        self.assertLess(fetch, idx("SIGNING=1"))
        self.assertLess(idx("SIGNING=1"), package)
        self.assertLess(package, idx("actions/upload-artifact@"))
        # the kit suite (tests/) holds test_install.py, test_app_python_lock.py and test_release_workflow.py
        for name in ("test_install.py", "test_app_python_lock.py", "test_release_workflow.py"):
            self.assertTrue((KIT / "tests" / name).is_file(), name)
        upload = self.step(self.build, "actions/upload-artifact@")
        self.assertEqual(upload["with"]["path"], "dist/")

    def test_tag_check_only_on_tags(self):
        check = self.step(self.build, "GITHUB_REF_NAME")
        self.assertEqual(check["if"], "github.event_name == 'push'")
        self.assertLess(self.step_index(self.build, "GITHUB_REF_NAME"),
                        self.step_index(self.build, "discover -s plugins/kanban/tests"))
        for rel in VERSION_FILES:
            self.assertIn(rel, check["run"])
        self.assertIn("kanban-v", check["run"])

    def test_tag_check_script_runs(self):
        """Run the step's script against the checkout: the current version passes, another one fails."""
        run = self.step(self.build, "GITHUB_REF_NAME")["run"]
        version = json.loads((KIT / "plugins/kanban/.claude-plugin/plugin.json").read_text())["version"]
        env = {**os.environ, "GITHUB_REF_NAME": f"kanban-v{version}"}
        ok = subprocess.run(["bash", "-c", run], cwd=KIT, env=env, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr + ok.stdout)
        env["GITHUB_REF_NAME"] = "kanban-v9.9.9"
        bad = subprocess.run(["bash", "-c", run], cwd=KIT, env=env, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("9.9.9", bad.stderr + bad.stdout)

    def test_concurrency(self):
        self.assertEqual(self.wf["concurrency"]["group"], "kanban-app-release-${{ github.ref }}")
        self.assertIs(self.wf["concurrency"]["cancel-in-progress"], False)


class PackageScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = PACKAGE.read_text()

    def test_codesign_verify(self):
        s = self.script
        self.assertIn("codesign --verify --deep --strict --verbose=2", s)
        # main flow: nested Mach-O signed before the bundle is built, verified after
        call = lambda pattern: re.search(pattern, s, re.M).start()  # noqa: E731
        self.assertLess(call(r'^sign_nested "\$PY_RES"$'), call(r"^run_build$"))
        self.assertLess(call(r"^run_build$"), call(r'^verify "\$app"$'))
        self.assertIn("--options runtime --entitlements", s)
        self.assertIn("--timestamp", s)
        self.assertIn('-s "$APPLE_SIGNING_IDENTITY"', s)
        self.assertIn("codesign --force -s -", s)
        self.assertIn("--target universal-apple-darwin --bundles app,dmg", s)
        self.assertIn("--config src-tauri/tauri.release.conf.json", s)

    def test_unsigned_dmg_holds_the_sealed_app(self):
        # Tauri builds its DMG before the outer ad-hoc seal: an unsigned release must rebuild the DMG from the app
        # that was sealed and verified, or Kanban.dmg ships an unsealed bundle (Gatekeeper: "damaged")
        s = self.script
        self.assertRegex(s, r'hdiutil create [^\n]*-srcfolder')
        self.assertLess(s.index("codesign --force -s - \"$app\""), s.index("hdiutil create"))
        self.assertLess(s.index('verify "$app"'), s.index("hdiutil create"))

    def test_bundled_python_smoke(self):
        s = self.script
        self.assertIn("import ssl, sqlite3, ctypes, json", s)
        self.assertRegex(s, r"\"\$exe\" -E -B -c 'import ssl, sqlite3, ctypes, json'")
        self.assertIn("arch -x86_64", s)
        self.assertIn("Rosetta", s)
        self.assertLess(s.index('smoke "$app"'), s.index("ditto -c -k --keepParent"))
        self.assertLess(s.index('verify "$app"'), s.index('smoke "$app"'))

    def test_sums_format(self):
        self.assertIn("shasum -a 256", self.script)

    def test_unknown_argument_exits_2(self):
        r = subprocess.run(["bash", str(PACKAGE), "--bogus"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", r.stderr)

    def test_missing_runtime_exits_1_naming_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "arm64" / "bin" / "python3"  # x86_64 missing
            fake.parent.mkdir(parents=True)
            fake.write_text("#!/bin/sh\n")
            fake.chmod(0o755)
            env = {**os.environ, "KANBAN_PY_RESOURCES": tmp, "DIST_DIR": str(Path(tmp) / "dist")}
            r = subprocess.run(["bash", str(PACKAGE), "--host-only"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("fetch-python.sh", r.stderr)
        self.assertIn("x86_64", r.stderr)


    def test_runtime_without_licences_exits_1(self):
        # a runtime fetched before Q18 has no licences/: packaging it would drop the texts NOTICE.md promises
        lock = json.loads((APP / "python.lock").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            for a in ("arm64", "x86_64"):
                fake = Path(tmp) / a / "bin" / "python3"
                fake.parent.mkdir(parents=True)
                fake.write_text("#!/bin/sh\n")
                fake.chmod(0o755)
                for rel in lock["licence_files"]:
                    if not rel.startswith("licences/"):
                        (Path(tmp) / a / rel).parent.mkdir(parents=True, exist_ok=True)
                        (Path(tmp) / a / rel).write_text("cpython licence")
            env = {**os.environ, "KANBAN_PY_RESOURCES": tmp, "DIST_DIR": str(Path(tmp) / "dist")}
            r = subprocess.run(["bash", str(PACKAGE), "--host-only"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("licence", r.stderr)
        self.assertIn("--licences-only", r.stderr)

class ConfigTests(unittest.TestCase):
    def test_entitlements_start_empty(self):
        data = plistlib.loads(ENTITLEMENTS.read_bytes())
        self.assertEqual(data, {})  # a key is added only when the signed smoke test fails (ADR-002)

    def test_release_config_entitlements(self):
        conf = json.loads(RELEASE_CONF.read_text())
        self.assertEqual(conf["bundle"]["macOS"], {"entitlements": "entitlements.plist"})
        self.assertEqual(conf["bundle"]["resources"], {"resources/python/arm64": "python/arm64",
                                                       "resources/python/x86_64": "python/x86_64"})


@unittest.skipUnless(os.environ.get("KANBAN_TEST_PACKAGE") == "1", "set KANBAN_TEST_PACKAGE=1 to build the app")
class PackageBuildTests(unittest.TestCase):
    def test_package_host_only(self):
        if subprocess.run(["cargo", "tauri", "--version"], capture_output=True).returncode != 0:
            self.skipTest("cargo tauri not installed")
        res = APP / "src-tauri" / "resources" / "python"
        if not all((res / a / "bin" / "python3").is_file() for a in ("arm64", "x86_64")):
            self.skipTest("bundled runtimes not fetched (fetch-python.sh --arch both)")
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            r = subprocess.run(["bash", str(PACKAGE), "--host-only"], capture_output=True, text=True,
                               env={**os.environ, "DIST_DIR": str(dist)})
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertTrue((dist / "Kanban.app.zip").is_file())
            sums = (dist / "SHA256SUMS").read_text().splitlines()
            self.assertTrue(sums)
            for line in sums:
                self.assertRegex(line, r"^[0-9a-f]{64}  (Kanban\.dmg|Kanban\.app\.zip)$")
            self.assertIn("Kanban.app.zip", "\n".join(sums))


if __name__ == "__main__":
    unittest.main()
