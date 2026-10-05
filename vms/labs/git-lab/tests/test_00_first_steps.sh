#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 0 — First steps (a repository from nothing)${RESET}"; echo ""
# The whole beginners' path, in a scratch directory so ~/first-repo of a learner stays untouched.
script=$(cat <<'EOS'
set -e
base=$(mktemp -d /tmp/first-steps.XXXXXX)
trap 'rm -rf "$base"' EXIT
cd "$base" && mkdir repo && cd repo && git init -q -b main
echo 'Shopping list' > list.txt && git add list.txt && git commit -q -m 'Start the shopping list'
echo 'milk' >> list.txt && echo 'bread' >> list.txt && git add list.txt && git commit -q -m 'Add milk and bread'
echo 'oops' >> list.txt && git restore list.txt
echo 'eggs' >> list.txt && git add list.txt && git restore --staged list.txt && git commit -q -am 'Add eggs'
git switch -q -c weekend && echo 'cake' >> list.txt && git commit -q -am 'Weekend: cake'
git switch -q main && git merge -q weekend && git branch -q -d weekend
echo 'scratch' > notes.tmp && printf '*.tmp\n' > .gitignore && git add .gitignore && git commit -q -m 'Ignore temporary files'
git init -q --bare "$base/remote.git" && git remote add origin "$base/remote.git" && git push -q -u origin main
git clone -q "$base/remote.git" "$base/clone"
echo 'coffee' >> list.txt && git commit -q -am 'Add coffee' && git push -q
git -C "$base/clone" pull -q
echo "commits=$(git rev-list --count main)"
echo "merges=$(git rev-list --merges --count main)"
echo "clone=$(git -C "$base/clone" rev-list --count main)"
echo "status=$(git status --porcelain | wc -l)"
echo "ignored=$(git status --porcelain --ignored | grep -c notes.tmp)"
echo "last=$(tail -1 "$base/clone/list.txt")"
EOS
)
out=$(on git-lab-server bash -c "$script" 2>&1 || true)
assert_contains "git is installed with an identity" "$(on git-lab-server 'git config --global user.name' || true)" "."
assert_contains "six commits on main" "$out" "^commits=6$"
assert_contains "the branch merged fast-forward: no merge commit" "$out" "^merges=0$"
assert_contains "the clone pulled the sixth commit" "$out" "^clone=6$"
assert_contains "the working tree is clean" "$out" "^status=0$"
assert_contains "notes.tmp is ignored" "$out" "^ignored=1$"
assert_contains "the clone has coffee" "$out" "^last=coffee$"

report_results "Exercise 0"
