#!/usr/bin/env bash
# Exercise 5 — a Compose app up, answering and down; the directory removed at the end.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 5 — a Compose app${RESET}"; echo ""

cleanup() { on docker-lab-server "cd ~/compose-demo 2>/dev/null && docker compose down >/dev/null 2>&1; rm -rf ~/compose-demo" >/dev/null 2>&1 || true; }
cleanup; trap cleanup EXIT

on docker-lab-server "mkdir -p ~/compose-demo/html && echo '<h1>hello from compose</h1>' > ~/compose-demo/html/index.html"
on docker-lab-server "cat > ~/compose-demo/compose.yaml" <<'EOF_COMPOSE'
services:
  web:
    image: nginx:1.27-alpine
    pull_policy: never
    ports:
      - "8081:80"
    volumes:
      - ./html:/usr/share/nginx/html:ro
EOF_COMPOSE
assert "docker compose up -d starts the app" on docker-lab-server "cd ~/compose-demo && docker compose up -d"
ps=$(on docker-lab-server "cd ~/compose-demo && docker compose ps --format '{{.Service}} {{.State}}'" 2>/dev/null || true)
assert_contains "the web service is running" "$ps" "^web running"
page=""
for _ in $(seq 1 10); do page=$(on docker-lab-server curl -s localhost:8081 2>/dev/null || true); grep -q compose <<<"$page" && break; sleep 1; done
assert_contains "it serves the bind-mounted page" "$page" "hello from compose"
assert "docker compose down stops it" on docker-lab-server "cd ~/compose-demo && docker compose down"

report_results "Exercise 5"
