#!/usr/bin/env bash
# Exercise 4 — a named volume and a bind mount outlive their containers; both removed at the end.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 4 — volumes and bind mounts${RESET}"; echo ""

cleanup() { on docker-lab-server "docker volume rm -f lab-data >/dev/null 2>&1; rm -rf ~/lab-bind" >/dev/null 2>&1 || true; }
cleanup; trap cleanup EXIT

assert "a named volume is created" on docker-lab-server docker volume create lab-data
on docker-lab-server docker run --rm --pull never -v lab-data:/data alpine:3.20 sh -c 'echo kept > /data/note'
note=$(on docker-lab-server docker run --rm --pull never -v lab-data:/data alpine:3.20 cat /data/note 2>/dev/null || true)
assert_contains "a second container reads what the first wrote" "$note" "^kept$"
on docker-lab-server "mkdir -p ~/lab-bind && docker run --rm --pull never -v ~/lab-bind:/out alpine:3.20 sh -c 'echo from-container > /out/stamp'"
stamp=$(on docker-lab-server cat lab-bind/stamp 2>/dev/null || true)
assert_contains "a bind mount puts the file on the host" "$stamp" "^from-container$"
cleanup
vols=$(on docker-lab-server docker volume ls -q 2>/dev/null || true)
assert_not_contains "the volume is gone after removal" "$vols" "^lab-data$"

report_results "Exercise 4"
