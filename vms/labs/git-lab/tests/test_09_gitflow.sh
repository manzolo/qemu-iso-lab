#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 9 — Git flow${RESET}"; echo ""
assert "git-flow is installed" on git-lab-server 'git flow version'
assert_contains "develop exists" "$(on git-lab-server 'cd ~/workspace && git branch' || true)" "develop"
assert_contains "git flow is initialised in .git/config" "$(on git-lab-server 'cd ~/workspace && git config --get gitflow.branch.develop' || true)" "develop"
assert_contains "the tag v1.0 exists" "$(on git-lab-server 'cd ~/workspace && git tag' || true)" "^v1\.0$"
assert_contains "v1.0 is reachable from main" "$(on git-lab-server 'cd ~/workspace && git log --format=%H main' || true)" "$(on git-lab-server 'cd ~/workspace && git rev-list -n 1 v1.0' || true)"
assert_contains "develop is on the remote" "$(on git-lab-server 'cd ~/workspace && git branch -r' || true)" "origin/develop"
assert_contains "main has the merge of release/1.0" "$(on git-lab-server 'cd ~/workspace && git log --oneline --merges main' || true)" "[Rr]elease.*1\.0|1\.0.*[Rr]elease"
assert_contains "the finished feature is in develop's history" "$(on git-lab-server 'cd ~/workspace && git log --oneline develop --grep=gitflow-demo' || true)" "gitflow-demo"
assert "gitflow-feature.txt is in develop" on git-lab-server 'cd ~/workspace && git show develop:gitflow-feature.txt'

report_results "Exercise 9"
