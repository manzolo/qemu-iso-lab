#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 1 — Git anatomy${RESET}"; echo ""
v=$(on git-lab-server git --version 2>&1 || true)
assert_contains "git is installed" "$v" "git version"
assert_contains "git user.name is set" "$(on git-lab-server git config --global user.name || true)" "."
assert_contains "git user.email is set" "$(on git-lab-server git config --global user.email || true)" "@"
assert "~/workspace is a repository" on git-lab-server 'test -d ~/workspace/.git'
assert "the bare remote exists" on git-lab-server test -d /srv/git/lab.git
assert_contains "the log has commits" "$(on git-lab-server 'cd ~/workspace && git log --oneline' || true)" "."
assert_contains "the working tree is clean" "$(on git-lab-server 'cd ~/workspace && git status' || true)" "nothing to commit|clean"

report_results "Exercise 1"
