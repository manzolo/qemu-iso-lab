#!/bin/bash
# git-lab: build the prepared repository for the current user (the lab's guest user).
#   ~/workspace            the working repository, on main, clean
#   /srv/git/lab.git       its bare remote ("origin")
# History: three base commits; feature/hello merged; feature/conflict with a resolved conflict on
# main; feature/stash (a stash demo, then rebased onto a hotfix on main); develop with a finished
# git-flow feature and the release 1.0 merged into main and tagged v1.0. Idempotent: run it again
# (or ~/reset-git-lab.sh) to start over. Ported from qlab's git-lab.
set -euo pipefail
me=$(id -un)
git config --global user.name "Lab User"
git config --global user.email "$me@git-lab.local"
git config --global init.defaultBranch main
git config --global advice.detachedHead false

sudo rm -rf /srv/git/lab.git
sudo mkdir -p /srv/git
sudo git init -q --bare /srv/git/lab.git
sudo chown -R "$me:$me" /srv/git

rm -rf ~/workspace
mkdir -p ~/workspace
cd ~/workspace
git init -q
git remote add origin /srv/git/lab.git

# ── Exercise 2: base commits ────────────────────────────────────────────────
echo "# Git Lab" > README.md
git add README.md && git commit -q -m "Initial commit: add README"
echo "Hello, Git!" > hello.txt
git add hello.txt && git commit -q -m "Add hello.txt"
echo "version=1.0" > version.txt
git add version.txt && git commit -q -m "Add version.txt"
git push -q -u origin main

# ── Exercises 3 and 4: feature/hello, merged ────────────────────────────────
git checkout -q -b feature/hello
echo "Hello from feature branch!" >> hello.txt
git add hello.txt && git commit -q -m "feature/hello: add greeting line"
git push -q origin feature/hello
git checkout -q main
git merge -q --no-ff feature/hello -m "Merge feature/hello into main"
git push -q origin main

# ── Exercise 5: a conflict, resolved ─────────────────────────────────────────
git checkout -q -b feature/conflict
echo "This is the feature version" > conflict.txt
git add conflict.txt && git commit -q -m "feature/conflict: add conflict.txt"
git push -q origin feature/conflict
git checkout -q main
echo "This is the main version" > conflict.txt
git add conflict.txt && git commit -q -m "main: add conflict.txt"
git merge -q feature/conflict >/dev/null 2>&1 || true        # conflicts on purpose
echo "Resolved: combined content from feature and main" > conflict.txt
git add conflict.txt && git commit -q -m "Merge: resolve conflict.txt conflict"
git push -q origin main

# ── Exercise 6: stash ────────────────────────────────────────────────────────
git checkout -q -b feature/stash
echo "Initial content" > wip.txt
git add wip.txt && git commit -q -m "feature/stash: initial wip.txt"
git push -q origin feature/stash
echo "Work in progress content" >> wip.txt
git stash push -q -m "WIP: stash demo"
git stash pop -q
git add wip.txt && git commit -q -m "feature/stash: finalize wip.txt after stash demo"
git push -q origin feature/stash

# ── Exercise 8: a hotfix on main, feature/stash rebased onto it ─────────────
git checkout -q main
echo "hotfix=true" >> version.txt
git add version.txt && git commit -q -m "hotfix: update version.txt"
git push -q origin main
git checkout -q feature/stash
git rebase -q main
git push -q --force origin feature/stash

# ── Exercise 9: git flow ─────────────────────────────────────────────────────
git checkout -q main
git checkout -q -b develop
git push -q origin develop
git checkout -q main
git flow init -d >/dev/null
git config gitflow.prefix.versiontag v
git flow feature start gitflow-demo >/dev/null
echo "Git flow feature content" > gitflow-feature.txt
git add gitflow-feature.txt && git commit -q -m "feature/gitflow-demo: add feature content"
git push -q origin feature/gitflow-demo
GIT_MERGE_AUTOEDIT=no git flow feature finish gitflow-demo >/dev/null
git push -q origin develop
git flow release start 1.0 >/dev/null
printf '\n## Release 1.0\n' >> README.md
git add README.md && git commit -q -m "release/1.0: prepare release notes"
git push -q origin release/1.0
GIT_MERGE_AUTOEDIT=no git flow release finish -m "Release version 1.0" 1.0 >/dev/null
git push -q origin main develop
git push -q origin v1.0

git checkout -q main
git fetch -q origin
echo "git-lab: repository ready in ~/workspace (remote /srv/git/lab.git)"
