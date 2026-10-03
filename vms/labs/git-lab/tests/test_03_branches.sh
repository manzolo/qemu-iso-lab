#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 3 — Branches${RESET}"; echo ""
local_b=$(on git-lab-server 'cd ~/workspace && git branch' || true)
remote_b=$(on git-lab-server 'cd ~/workspace && git branch -r' || true)
assert_contains "feature/hello exists locally" "$local_b" "feature/hello"
assert_contains "feature/hello exists on the remote" "$remote_b" "origin/feature/hello"
assert_contains "develop exists locally" "$local_b" "develop"
assert_contains "develop exists on the remote" "$remote_b" "origin/develop"
assert_contains "feature/stash exists locally" "$local_b" "feature/stash"
assert_contains "main exists on the remote" "$remote_b" "origin/main"
assert_contains "a branch is a file holding one hash" "$(on git-lab-server 'cat ~/workspace/.git/refs/heads/main' || true)" "^[0-9a-f]{40}$"
assert_contains "HEAD names the current branch" "$(on git-lab-server 'cat ~/workspace/.git/HEAD' || true)" "^ref: refs/heads/"

report_results "Exercise 3"
