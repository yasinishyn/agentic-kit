#!/usr/bin/env bash
# Unit tests for guard-bash.sh. Run from the project root: bash .claude/hooks/test_guard_bash.sh
# Each case pipes a PreToolUse JSON payload into the hook and checks the exit code (0 allow, 2 block).
# When you add an entry to the CONFIG block in guard_bash.py, add a "must BLOCK" case for it here.
set -u
HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/guard-bash.sh"
pass=0; fail=0
check() { # expected_rc  description  command
  local expected="$1" desc="$2" cmd="$3" payload rc
  payload=$(python3 -c 'import json,sys; print(json.dumps({"tool_name":"Bash","tool_input":{"command":sys.argv[1]}}))' "$cmd")
  printf '%s' "$payload" | "$HOOK" >/dev/null 2>&1; rc=$?
  if [ "$rc" -eq "$expected" ]; then pass=$((pass+1)); printf 'ok   %-4s %s\n' "$rc" "$desc"
  else fail=$((fail+1)); printf 'FAIL got=%s want=%s %s :: %s\n' "$rc" "$expected" "$desc" "$cmd"; fi
}
# --- must BLOCK (2) ---
check 2 "git push"                         "git push origin feature/my-branch"
check 2 "git -C push"                      "git -C /path/to/repo push --force"
check 2 "git push inside bash -c"          "bash -c 'cd repo && git push'"
check 2 "eval git push"                    "eval git push origin main"
check 2 "deploy script direct"             "./deploy/release.sh"
check 2 "deploy script via bash"           "bash deploy/deploy-prod.sh"
check 2 "deploy script via sh"             "sh deploy/restore-env.sh"
check 2 "deploy script via python"         "python3 deploy/publish.py"
check 2 "aws cli"                          "aws s3 ls"
check 2 "aws after env assignment"         "AWS_PROFILE=x aws secretsmanager get-secret-value --secret-id y"
check 2 "aws behind wrapper"               "timeout 30 aws sts get-caller-identity"
check 2 "psql remote host"                 "psql -h db.example.com -U u d"
check 2 "psql remote url"                  "psql postgresql://u:p@remote.example.com:25060/defaultdb"
check 2 "pg_dump remote host"              "pg_dump --host=db.example.com appdb"
check 2 "cat ~/.aws/credentials"           "cat ~/.aws/credentials"
check 2 "read ssh key"                     "cat \$HOME/.ssh/id_ed25519"
check 2 "source .env"                      "source .env"
check 2 "cat .env.production"              "cat deploy/.env.production"
check 2 "gh pr create"                     "gh pr create --fill"
check 2 "gh release create"                "gh release create v1.0.0"
# --- must ALLOW (0) ---
check 0 "git status"                       "git status --short"
check 0 "git log"                          "git log --oneline -5"
check 0 "git commit mentioning push"       "git commit -m 'describe push behaviour'"
check 0 "grep for DATABASE_URL"            "grep -rn DATABASE_URL src/"
check 0 "psql localhost"                   "psql -h 127.0.0.1 -p 5432 -U app appdb -c 'select 1'"
check 0 "psql local url"                   "psql postgresql://app:x@localhost:5432/appdb"
check 0 "cat .env.example"                 "cat .env.example"
check 0 "run tests"                        "python -m pytest tests -q"
check 0 "read deploy script"               "head -20 deploy/release.sh; ls deploy"
check 0 "curl local health"                "curl -fsS http://127.0.0.1:8080/health"
check 0 "gh pr view"                       "gh pr view 12"
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
