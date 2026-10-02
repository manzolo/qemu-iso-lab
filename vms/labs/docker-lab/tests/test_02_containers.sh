#!/usr/bin/env bash
# Exercise 2 — a one-shot container and a detached web server; removed at the end.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 2 — images and containers${RESET}"; echo ""

cleanup() { on docker-lab-server docker rm -f lab-web >/dev/null 2>&1 || true; }
cleanup; trap cleanup EXIT

os=$(on docker-lab-server docker run --rm --pull never alpine:3.20 cat /etc/os-release 2>/dev/null || true)
assert_contains "a one-shot alpine container runs" "$os" "Alpine Linux"
assert "nginx starts detached on port 8080" on docker-lab-server docker run -d --name lab-web -p 8080:80 --pull never nginx:1.27-alpine
running=$(on docker-lab-server docker ps --format '{{.Names}}' 2>/dev/null || true)
assert_contains "docker ps lists it" "$running" "^lab-web$"
page=""
for _ in $(seq 1 10); do page=$(on docker-lab-server curl -s localhost:8080 2>/dev/null || true); grep -q "nginx" <<<"$page" && break; sleep 1; done
assert_contains "the published port answers with nginx's page" "$page" "Welcome to nginx"
cleanup
left=$(on docker-lab-server docker ps -a --format '{{.Names}}' 2>/dev/null || true)
assert_not_contains "docker rm -f leaves nothing behind" "$left" "^lab-web$"

report_results "Exercise 2"
