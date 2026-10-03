#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 5 — Conflicts${RESET}"; echo ""
assert "conflict.txt exists" on git-lab-server 'test -f ~/workspace/conflict.txt'
c=$(on git-lab-server 'cat ~/workspace/conflict.txt' || true)
assert_not_contains "no <<<<<<< marker left" "$c" "<<<<<<<"
assert_not_contains "no >>>>>>> marker left" "$c" ">>>>>>>"
assert_contains "conflict.txt has the resolved content" "$c" "Resolved"
assert_contains "main has the resolving merge commit" "$(on git-lab-server 'cd ~/workspace && git log --oneline main' || true)" "resolve|Merge"
assert_contains "feature/conflict is on the remote" "$(on git-lab-server 'cd ~/workspace && git branch -r' || true)" "origin/feature/conflict"
assert_contains "the two sides differ on conflict.txt" "$(on git-lab-server 'cd ~/workspace && git diff feature/conflict...main -- conflict.txt' || true)" "feature version|Resolved"

report_results "Exercise 5"
