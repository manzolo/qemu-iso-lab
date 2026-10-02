#!/usr/bin/env bash
# Exercise 3 — exec, logs and inspect on a running container; removed at the end.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 3 — inside a running container${RESET}"; echo ""

cleanup() { on docker-lab-server docker rm -f lab-web >/dev/null 2>&1 || true; }
cleanup; trap cleanup EXIT

on docker-lab-server docker run -d --name lab-web --pull never nginx:1.27-alpine >/dev/null
sleep 1
v=$(on docker-lab-server docker exec lab-web nginx -v 2>&1 || true)
assert_contains "exec runs a second process inside" "$v" "nginx/1\.27"
assert "exec sees the container's own files" on docker-lab-server docker exec lab-web test -f /usr/share/nginx/html/index.html
on docker-lab-server "docker exec lab-web wget -qO- localhost >/dev/null" || true
logs=$(on docker-lab-server docker logs lab-web 2>&1 || true)
assert_contains "logs show the main process's output" "$logs" "GET / HTTP"
state=$(on docker-lab-server docker inspect -f '{{.State.Status}}' lab-web 2>/dev/null || true)
assert_contains "inspect reports it running" "$state" "^running$"

report_results "Exercise 3"
