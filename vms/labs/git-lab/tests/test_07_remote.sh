#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 7 — Remote${RESET}"; echo ""
rv=$(on git-lab-server 'cd ~/workspace && git remote -v' || true)
assert_contains "origin is configured" "$rv" "origin"
assert_contains "origin is the bare repository" "$rv" "/srv/git/lab.git"
assert_contains "the bare repository holds objects" "$(on git-lab-server 'ls /srv/git/lab.git/objects' || true)" "."
assert "git fetch origin succeeds" on git-lab-server 'cd ~/workspace && git fetch origin'
assert_contains "origin/main has commits" "$(on git-lab-server 'cd ~/workspace && git log --oneline origin/main' || true)" "."
assert_contains "main matches origin/main" "$(on git-lab-server 'cd ~/workspace && git rev-parse main' || true)" "$(on git-lab-server 'cd ~/workspace && git rev-parse origin/main' || true)"
assert_contains "ls-remote lists the refs" "$(on git-lab-server 'cd ~/workspace && git ls-remote origin' || true)" "refs/heads/main"

report_results "Exercise 7"
