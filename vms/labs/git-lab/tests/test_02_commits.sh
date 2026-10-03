#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 2 — Commits${RESET}"; echo ""
n=$(on git-lab-server 'cd ~/workspace && git log --oneline main | wc -l' || true)
assert_contains "main has at least 3 commits" "$n" "^\s*([3-9]|[0-9]{2,})$"
assert_contains "git log --oneline shows hashes" "$(on git-lab-server 'cd ~/workspace && git log --oneline -1' || true)" "^[0-9a-f]"
empty=$(on git-lab-server 'cd ~/workspace && git log --format=%s | grep -c "^$" || true')
assert_contains "no empty commit message" "${empty:-0}" "^\s*0$"
for f in README.md hello.txt version.txt; do
    assert_contains "$f has commits" "$(on git-lab-server "cd ~/workspace && git log --oneline -- $f" || true)" "."
done
assert_contains "a commit object has a tree and an author" "$(on git-lab-server 'cd ~/workspace && git cat-file -p HEAD' || true)" "^tree [0-9a-f]+"

report_results "Exercise 2"
