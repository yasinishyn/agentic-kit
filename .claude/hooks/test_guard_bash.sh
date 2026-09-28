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
check 2 "git commit mentioning push"       "git commit -m 'describe push behaviour'"
check 0 "grep for DATABASE_URL"            "grep -rn DATABASE_URL src/"
check 0 "psql localhost"                   "psql -h 127.0.0.1 -p 5432 -U app appdb -c 'select 1'"
check 0 "psql local url"                   "psql postgresql://app:x@localhost:5432/appdb"
check 0 "cat .env.example"                 "cat .env.example"
check 0 "run tests"                        "python -m pytest tests -q"
check 0 "read deploy script"               "head -20 deploy/release.sh; ls deploy"
check 0 "curl local health"                "curl -fsS http://127.0.0.1:8080/health"
check 0 "gh pr view"                       "gh pr view 12"
# --- git: stage yes, commit/push no (default ALLOW_AGENT_COMMITS=False) ---
check 0 "git add"                          "git add -A && git add src/app.py"
check 0 "git status / diff / log"          "git status -sb && git diff --stat && git log -3"
check 2 "git commit"                       "git commit -m wip"
check 2 "git -C commit"                    "git -C /tmp/repo commit -q -F msg.txt"
check 2 "git commit via bash -c"           "bash -c 'git add . && git commit -m x'"
check 2 "git merge / rebase / pull"        "git pull --rebase"
check 2 "git cherry-pick"                  "git cherry-pick abc123"
check 2 "set git identity"                 "git config user.email bot@example.com"
# --- kanban board: the UI token and the human-only API endpoints (architecture §8) ---
check 2 "cat kanban ui.token"              "cat ~/Library/Application\\ Support/Kanban/ui.token"
check 2 "cat quoted kanban ui.token"       "cat \"\$HOME/Library/Application Support/Kanban/ui.token\""
check 2 "head KANBAN_HOME ui.token"        "head -c 64 \"\$KANBAN_HOME/ui.token\""
check 2 "less xdg kanban ui.token"         "less \${XDG_DATA_HOME}/kanban/ui.token"
check 2 "cd kanban home then cat"          "cd ~/Library/Application\\ Support/Kanban && cat ui.token"
check 2 "python open ui.token"             "python3 -c \"print(open('/Users/me/Library/Application Support/Kanban/ui.token').read())\""
check 2 "token into a variable"            "T=\$(cat ~/Library/Application\\ Support/Kanban/ui.token); echo ok"
check 2 "curl approve"                     "curl -X POST -H 'Authorization: Bearer x' http://127.0.0.1:47821/api/projects/p1/tickets/t/approve"
check 2 "curl move on localhost"           "curl -s -XPOST localhost:47821/api/projects/p1/tickets/t/move -d '{\"stage\":\"developer\"}'"
check 2 "curl port variable"               "curl \"http://127.0.0.1:\$PORT/api/projects/p1/tickets/t/move\""
check 2 "wget files PUT"                   "wget --method=PUT -O- http://127.0.0.1:47821/api/projects/p1/files?path=x.md"
check 2 "httpie runs start"                "http POST 127.0.0.1:47821/api/projects/p1/tickets/t/runs/start"
check 2 "curl run stop"                    "curl -X POST http://localhost:47821/api/runs/r1/stop"
check 2 "python urllib approve"            "python3 -c \"import urllib.request as u; u.urlopen('http://127.0.0.1:47821/api/projects/p/tickets/t/approve', b'{}')\""
check 2 "python requests move"             "python -c \"import requests; requests.post('http://localhost:47821/api/projects/p/tickets/t/move')\""
check 2 "curl inside bash -c"              "bash -c 'curl -X POST http://127.0.0.1:47821/api/projects/p/tickets/t/approve'"
check 0 "curl daemon health"               "curl -fsS http://127.0.0.1:47821/api/health"
check 0 "curl read-only board"             "curl -s -H 'Authorization: Bearer x' http://127.0.0.1:47821/api/projects/p1/board"
check 0 "curl remote api move"             "curl https://example.com/api/tickets/move"
check 0 "grep kanban sources for ui.token" "grep -rn 'ui.token' plugins/kanban/scripts"
check 0 "list kanban home"                 "ls -la ~/Library/Application\\ Support/Kanban"
check 0 "read daemon.json"                 "cat ~/Library/Application\\ Support/Kanban/daemon.json"
check 0 "run kanban tests"                 "python3 plugins/kanban/tests/test_runs_api.py"
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
