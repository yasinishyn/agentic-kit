#!/usr/bin/env python3
"""Claude Code PreToolUse checker for Bash commands (agent guardrails).

Reads the hook JSON from stdin. Exit 0 = allow, exit 2 = block (reason on stderr).
Any unexpected error exits 2 via guard-bash.sh (fail-closed).

Always blocked (humans do these, never agents):
  * git push in any form (incl. `git -C dir push`, inside `bash -c`, via `eval`)
  * GitHub PR/release/repo writes: gh pr create/merge, gh release create, ...
  * the `aws` CLI
  * running anything under a deploy/ directory (./deploy/x.sh, bash deploy/x.sh, python deploy/x.py)
  * psql / pg_dump / pg_restore / mysql ... against a non-local host
  * reading or sourcing credential files: ~/.aws, ~/.ssh, .env and .env.* (except .env.example/.template/.sample)

Project-specific rules live in the CONFIG block below (empty by default).
See README.md next to this file for the rule list and tests.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

# =====================================================================================
# PROJECT CONFIG — edit these lists for your project, add a test case for each entry to
# test_guard_bash.sh, run it, and have a human review the diff. All lists are ADDITIVE:
# they can only block more (or, for EXTRA_LOCAL_HOSTS, treat more hosts as local).
# =====================================================================================

# Hostnames that count as "local" database hosts, in addition to localhost/127.0.0.1/::1/*.localhost.
# Typically your docker-compose service or container names.
EXTRA_LOCAL_HOSTS: list[str] = [
    # "db", "postgres", "myapp-local-postgres",
]

# Environment variables agents must never assign or export (secrets wiring for shared/prod systems).
# Regex fragments, matched as `NAME=` / `export NAME=`.
FORBIDDEN_ENV_VARS: list[str] = [
    # "PROD_DATABASE_URL", r"LEGACY_[A-Z_]+", "APP_SECRET_ARN",
]

# Executables or scripts only humans run, besides deploy/*. Regexes searched in the executable path
# (or the script passed to bash/sh/python/node/...).
FORBIDDEN_EXECUTABLES: list[str] = [
    # r"push-to-mirror", r"(^|/)scripts/release\.sh$", r"(^|/)bin/prod-",
]

# Scripts that write to the database named by DB_URL_ENV_VAR (often read from .env, which may point at a
# shared database). Blocked on the host unless an inline local URL is given, e.g.
# `DATABASE_URL=postgresql://u@127.0.0.1:5432/app python scripts/seed_users.py`. Regexes on the script path.
DB_WRITING_SCRIPTS: list[str] = [
    # r"(^|/)scripts/(seed_\w+|load_\w+|backfill_\w+|migrate_\w+)\.py$",
]
DB_URL_ENV_VAR = "DATABASE_URL"

# Extra credential/secret file patterns (regexes on each argument of cat/less/grep/source/cp/...).
EXTRA_SECRET_PATHS: list[str] = [
    # r"(^|/)deploy/\.env", r"(^|/)secrets/", r"\.pem$",
]

# ===================================== END CONFIG =====================================

BASE_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
INTERPRETERS = {"python", "python3", "node", "ruby", "perl", "php", "deno", "bun", "npx", "tsx", "ts-node"}
WRAPPERS = {"sudo", "time", "exec", "nohup", "env", "command", "xargs", "nice", "timeout"}
READERS = {
    "cat", "less", "more", "head", "tail", "bat", "strings", "xxd", "od", "base64",
    "cp", "scp", "rsync", "source", ".", "grep", "rg", "awk", "sed", "vi", "vim", "nano", "open",
}
DB_CLIENTS = {"psql", "pg_dump", "pg_restore", "pg_dumpall", "createdb", "dropdb", "mysql", "mariadb", "mysqldump"}
BASE_SECRET_PATH_RE = r"(^|/|~)\.aws(/|$)|(^|/|~)\.ssh(/|$)|(^|/)\.env(\.[\w.-]+)?$"
SAFE_ENV_EXAMPLE_RE = re.compile(r"(^|/)\.env\.(example|template|sample|dist)$")
DEPLOY_RE = re.compile(r"(^|/)deploy/\S+")
GIT_PUSH_RE = re.compile(r"\bgit\b(\s+(-C|-c|--git-dir|--work-tree)\s*=?\s*\S+|\s+--?[\w-]+(=\S+)?)*\s+push\b")
URL_HOST_RE = re.compile(r"(?:postgres(?:ql)?|mysql|mariadb)://(?:[^@/\s]*@)?\[?([^:/?\s\]]+)")
SPLIT_RE = re.compile(r"\|\||&&|;|\||\n|&(?!&)")


def _any(patterns: list[str]) -> re.Pattern[str] | None:
    return re.compile("|".join(f"(?:{p})" for p in patterns)) if patterns else None


def block(reason: str) -> None:
    sys.stderr.write(
        f"BLOCKED by agent guard: {reason}\n"
        "Policy: agents never push, deploy, publish, call cloud CLIs, touch non-local databases or read "
        "credentials. Ask the user to run it themselves if it is genuinely needed.\n"
    )
    sys.exit(2)


def host_is_local(host: str) -> bool:
    h = host.strip().lower().strip("'\"[]")
    return h in BASE_LOCAL_HOSTS or h in {x.lower() for x in EXTRA_LOCAL_HOSTS} or h.endswith(".localhost")


def strip_wrappers(tokens: list[str]) -> list[str]:
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            i += 1
            continue
        if t in WRAPPERS:
            i += 1
            # skip wrapper options like `timeout 30`, `env -i`
            while i < len(tokens) and (tokens[i].startswith("-") or re.match(r"^\d+[smh]?$", tokens[i])):
                i += 1
            continue
        break
    return tokens[i:]


def inline_env(tokens: list[str]) -> dict[str, str]:
    env = {}
    for t in tokens:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", t)
        if not m:
            break
        env[m.group(1)] = m.group(2)
    return env


def first_script(args: list[str]) -> str:
    """First non-option argument (the script an interpreter runs); `-m mod` / `-c code` mean no script file."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in {"-m", "-c", "-e", "--eval"}:
            return ""
        if not a.startswith("-"):
            return a
        i += 1
    return ""


def check_segment(seg: str, depth: int = 0) -> None:
    seg = seg.strip()
    if not seg:
        return
    try:
        tokens = shlex.split(seg, posix=True)
    except ValueError:
        tokens = seg.split()
    if not tokens:
        return
    env = inline_env(tokens)
    body = strip_wrappers(tokens)
    if not body:
        return
    cmd = os.path.basename(body[0])
    args = body[1:]

    # Nested shells: bash -c "..."; or `bash deploy/x.sh`
    if cmd in SHELLS:
        if "-c" in args:
            idx = args.index("-c")
            if idx + 1 < len(args) and depth < 3:
                check_command(args[idx + 1], depth + 1)
            return
        script = first_script(args)
        if script:
            check_executable(script, env)
        return

    if cmd == "eval" and depth < 3:
        check_command(" ".join(args), depth + 1)
        return

    check_executable(body[0], env)
    if cmd in INTERPRETERS or re.match(r"^python3?(\.\d+)?$", cmd):
        script = first_script(args)
        if script:
            check_executable(script, env)

    if cmd == "aws":
        block("the `aws` CLI is human-only for agents; use a local emulator in your local environment.")
    if cmd == "gh" and len(args) >= 2 and args[0] in {"pr", "release", "repo"} and args[1] in {
        "create", "merge", "sync", "edit", "delete", "upload"
    }:
        block("GitHub PR/release/repo writes are human-only.")
    if cmd in DB_CLIENTS:
        check_db_hosts(args)
    if cmd in READERS:
        secret_re = re.compile("|".join([BASE_SECRET_PATH_RE, *[f"(?:{p})" for p in EXTRA_SECRET_PATHS]]))
        for a in args:
            expanded = a.replace("$HOME", "~").replace("${HOME}", "~")
            if secret_re.search(expanded) and not SAFE_ENV_EXAMPLE_RE.search(expanded):
                block(f"reading or sourcing credential files is not allowed ({a}).")


def check_executable(exe: str, env: dict[str, str]) -> None:
    norm = exe[2:] if exe.startswith("./") else exe
    if DEPLOY_RE.search(norm):
        block(f"deploy scripts are human-only ({exe}).")
    forbidden = _any(FORBIDDEN_EXECUTABLES)
    if forbidden and forbidden.search(norm):
        block(f"`{exe}` is human-only (guard CONFIG: FORBIDDEN_EXECUTABLES).")
    db_scripts = _any(DB_WRITING_SCRIPTS)
    if db_scripts and db_scripts.search(norm):
        m = URL_HOST_RE.search(env.get(DB_URL_ENV_VAR, ""))
        if not (m and host_is_local(m.group(1))):
            block(
                f"`{exe}` writes to {DB_URL_ENV_VAR}, which may point at a shared database via .env. "
                f"Run it inside your local environment or pass an inline local {DB_URL_ENV_VAR}=...@127.0.0.1:.../..."
            )


def check_db_hosts(args: list[str]) -> None:
    hosts = []
    for i, a in enumerate(args):
        if a in {"-h", "--host"} and i + 1 < len(args):
            hosts.append(args[i + 1])
        elif a.startswith("--host="):
            hosts.append(a.split("=", 1)[1])
        elif a.startswith("-h") and len(a) > 2:
            hosts.append(a[2:])
        for m in URL_HOST_RE.finditer(a):
            hosts.append(m.group(1))
        m = re.search(r"(?:^|\s)host=([^\s]+)", a)
        if m:
            hosts.append(m.group(1))
    for h in hosts:
        if not host_is_local(h):
            block(f"database clients may only target local hosts (got {h}).")


def check_command(command: str, depth: int = 0) -> None:
    if GIT_PUSH_RE.search(command):
        block("git push is human-only.")
    forbidden_env = _any(FORBIDDEN_ENV_VARS)
    if forbidden_env and re.search(rf"(^|[\s;&|(])(export\s+)?(?:{forbidden_env.pattern})=", command):
        block("setting this environment variable is not allowed for agents (guard CONFIG: FORBIDDEN_ENV_VARS).")
    for seg in SPLIT_RE.split(command):
        check_segment(seg, depth)


def main() -> None:
    raw = sys.stdin.read()
    data = json.loads(raw)
    if data.get("tool_name") not in (None, "Bash"):
        sys.exit(0)
    command = (data.get("tool_input") or {}).get("command", "")
    if not isinstance(command, str):
        block("unexpected hook payload.")
    check_command(command)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail closed
        sys.stderr.write(f"guard-bash: checker error ({exc!r}); blocking by default.\n")
        sys.exit(2)
