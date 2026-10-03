#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 8 — Rebase${RESET}"; echo ""
ahead=$(on git-lab-server 'cd ~/workspace && git log --oneline main..feature/stash | wc -l' || true)
assert_contains "feature/stash is ahead of main" "$ahead" "^\s*[1-9]"
merges=$(on git-lab-server 'cd ~/workspace && git log --merges main..feature/stash --oneline | wc -l' || true)
assert_contains "no merge commit between main and feature/stash: linear" "$merges" "^\s*0$"
# main moved on after the rebase (the release 1.0 merge): the branch point is the hotfix commit.
assert_contains "feature/stash was rebased onto the hotfix" "$(on git-lab-server 'cd ~/workspace && git log -1 --format=%s $(git merge-base main feature/stash)' || true)" "hotfix"
assert_contains "the hotfix is in feature/stash's history" "$(on git-lab-server 'cd ~/workspace && git log --oneline feature/stash' || true)" "hotfix"
assert "wip.txt exists on feature/stash" on git-lab-server 'cd ~/workspace && git show feature/stash:wip.txt'

report_results "Exercise 8"
