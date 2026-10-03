#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 6 — Stash${RESET}"; echo ""
cleanup() { on git-lab-server 'cd ~/workspace && git checkout -q -- wip.txt 2>/dev/null; git stash drop -q 2>/dev/null; git checkout -q main' >/dev/null 2>&1 || true; }
trap cleanup EXIT
on git-lab-server 'cd ~/workspace && git checkout -q feature/stash' >/dev/null
on git-lab-server 'echo "temporary work" >> ~/workspace/wip.txt' >/dev/null
on git-lab-server 'cd ~/workspace && git stash push -q -m "test: stash exercise"' >/dev/null
assert_contains "the stash list has the entry" "$(on git-lab-server 'cd ~/workspace && git stash list' || true)" "stash@"
assert_not_contains "the change is hidden" "$(on git-lab-server 'cat ~/workspace/wip.txt' || true)" "temporary work"
on git-lab-server 'cd ~/workspace && git stash pop -q' >/dev/null
assert_contains "pop brings the change back" "$(on git-lab-server 'cat ~/workspace/wip.txt' || true)" "temporary work"
on git-lab-server 'cd ~/workspace && git checkout -q -- wip.txt' >/dev/null
assert_contains "wip.txt was committed on feature/stash" "$(on git-lab-server 'cd ~/workspace && git log --oneline feature/stash -- wip.txt' || true)" "."

report_results "Exercise 6"
