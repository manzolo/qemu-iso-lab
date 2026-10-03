#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 4 — Merge${RESET}"; echo ""
assert_contains "feature/hello is merged into main" "$(on git-lab-server 'cd ~/workspace && git branch --merged main' || true)" "feature/hello"
assert_contains "hello.txt carries the feature line" "$(on git-lab-server 'cat ~/workspace/hello.txt' || true)" "Hello from feature branch"
assert_not_contains "no unmerged paths" "$(on git-lab-server 'cd ~/workspace && git status' || true)" "Unmerged paths|both modified|both added"
assert_contains "main has the merge commit of feature/hello" "$(on git-lab-server 'cd ~/workspace && git log --oneline --merges main' || true)" "feature/hello"
assert_contains "a merge commit has two parents" "$(on git-lab-server 'cd ~/workspace && git log --merges --format=%P -1 main' || true)" "^[0-9a-f]+ [0-9a-f]+$"
assert_contains "main is clean" "$(on git-lab-server 'cd ~/workspace && git checkout -q main && git status' || true)" "nothing to commit|clean"

report_results "Exercise 4"
