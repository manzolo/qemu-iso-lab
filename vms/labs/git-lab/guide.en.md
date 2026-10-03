# Git lab: commits, branches, merge, conflicts, stash, rebase and git flow

One server with a repository already made for you, and Git looked at from underneath before it is
practised: a commit is an object you can read, a branch is a file with one hash in it, the index sits
between your files and the history. Ported from qlab's git-lab.

| VM | What it holds | SSH from the host |
|---|---|---|
| `git-lab-server` | `~/workspace` (the working repository, on `main`) and `/srv/git/lab.git` (its bare remote, `origin`) | `vmctl shell git-lab-server` (127.0.0.1:2365) |

The repository's history, built by the install (`~/setup-git-lab.sh`): three base commits; `feature/hello`
merged into `main`; `feature/conflict` and a conflict on `conflict.txt` resolved on `main`; `feature/stash`
with a stash demo, rebased onto a hotfix on `main`; `develop` with a finished git-flow feature and the
release 1.0 merged into `main` and tagged `v1.0`. The exercises change history on purpose:
**`bash ~/reset-git-lab.sh` rebuilds everything** in a few seconds.

## Start

```sh
vmctl group install git-lab      # one cloud image, about a minute after the download
vmctl shell git-lab-server
cd ~/workspace
```

## Exercise 1: Git anatomy

A repository is a `.git/` directory: `objects` (every version of every file, and every commit),
`refs` (branches and tags), `HEAD` (where you are) and `config`. The bare repository is the same
thing without a working tree beside it: that is all a Git server is.

```sh
ls -a .git
git config --global --list
git status; git branch
ls /srv/git/lab.git
```

## Exercise 2: commits are objects

`git cat-file -p` prints any object. A commit holds a tree, its parent(s), an author, a committer and
a message; the tree is a directory listing (mode, type, hash, name); the blob is the content and
nothing else. A merge commit is simply a commit with two parents. Nothing is ever edited in place:
`--amend` makes a new commit with a new hash and moves the branch to it.

```sh
git log --oneline --graph --all
git cat-file -p HEAD
git cat-file -p HEAD^{tree}
git cat-file -p HEAD:hello.txt
echo 'My notes' > notes.txt && git add notes.txt && git commit -m 'Add notes.txt'
git commit --amend -m 'docs: add notes.txt'
git reset --hard HEAD~1
```

## Exercise 3: branches are pointers

`cat .git/refs/heads/main` is the whole branch: 40 hexadecimal characters. `HEAD` names the branch
you are on; a "detached HEAD" holds a hash instead. `remotes/origin/*` are your last record of the
remote's branches, refreshed by `fetch`, not the remote itself.

```sh
git branch -a
cat .git/HEAD; cat .git/refs/heads/main
git branch --merged main
git switch -c feature/my-feature && git switch main
git branch -d feature/my-feature
```

## Exercise 4: merge, fast-forward or a merge commit

When `main` has not moved since the branch was made, merging just moves the pointer forward. When both
sides moved, Git writes a merge commit with two parents; `--no-ff` asks for one even when it could
fast-forward, to keep the feature visible in the graph.

```sh
git log --merges --oneline
git show --stat HEAD~3
git switch -c feature/test-merge && echo test > merge-test.txt && git add merge-test.txt && git commit -m 'test: add merge-test.txt'
git switch main && git merge --no-ff feature/test-merge -m 'Merge feature/test-merge'
git log --oneline --graph -4
git reset --hard origin/main && git branch -D feature/test-merge
```

## Exercise 5: conflicts

Two branches changed the same lines of `conflict.txt`. Git stopped the merge and wrote both versions
between `<<<<<<<`, `=======` and `>>>>>>>`; the lab resolved it and committed. Make your own: branch,
change the same line on both sides, merge, read the markers, write the content you want, add, commit.

```sh
cat conflict.txt
git log --oneline --grep=resolve
git diff feature/conflict...main -- conflict.txt
git switch -c branch-a && echo 'Version A' > clash.txt && git add clash.txt && git commit -m 'branch-a: clash.txt'
git switch main && echo 'Version B' > clash.txt && git add clash.txt && git commit -m 'main: clash.txt'
git merge branch-a || cat clash.txt
echo 'Resolved version' > clash.txt && git add clash.txt && git commit -m 'Merge: resolve clash.txt'
git reset --hard origin/main && git branch -D branch-a
```

## Exercise 6: stash

`git stash` puts your uncommitted changes on a stack and leaves the working tree clean, so you can
switch branch or pull; `git stash pop` brings the newest entry back. Stashes are local: they never go
to a remote. `-u` includes untracked files.

```sh
git switch feature/stash
echo 'More work in progress' >> wip.txt && git stash push -m 'WIP: my in-progress work'
git status --short; cat wip.txt
git stash list
git stash pop && cat wip.txt
git checkout -- wip.txt && git switch main
```

## Exercise 7: remote

`origin` is the bare repository. `push` uploads your commits and moves the remote's branch; `fetch`
downloads the remote's refs into `remotes/origin/*` without touching your branches; `pull` is fetch
plus merge. The two ranges below are the question to ask before either: what is there that is not
here, and what is here that is not there.

```sh
git remote -v
git fetch origin && git log --oneline HEAD..origin/main
git log --oneline origin/main..HEAD
git ls-remote origin
echo pushed > pushed.txt && git add pushed.txt && git commit -m 'Add pushed.txt' && git push origin main
git reset --hard HEAD~1 && git push --force origin main
```

The last line rewrites the remote's `main`; it is fine on your own lab remote and a bad habit anywhere
shared.

## Exercise 8: rebase

`rebase` replays a branch's commits on top of another branch: the result is a straight line with no
merge commit, and new hashes. `feature/stash` was rebased onto the hotfix on `main`. The rule: never
rebase commits other people already have.

```sh
git log --oneline feature/stash
git log --merges main..feature/stash --oneline
git log --oneline --graph main feature/stash
git switch -c feature/rebase-demo main~2 && echo 'old base' > rebase-demo.txt && git add rebase-demo.txt && git commit -m 'demo commit'
git rebase main && git log --oneline -3
git switch main && git branch -D feature/rebase-demo
```

## Exercise 9: git flow

A branching model with roles: `main` holds releases, `develop` integrates, `feature/*` branches start
from `develop` and finish into it, `release/*` finishes into both `main` and `develop` and leaves a
tag, `hotfix/*` starts from `main`. The `git-flow` CLI does the branching, merging, tagging and
cleanup; its settings live in `.git/config`.

```sh
git flow version
git config --get-regexp gitflow
git tag; git log --oneline --merges main
git log --oneline --graph develop -8
git flow feature start try-me && echo try > try.txt && git add try.txt && git commit -m 'feature/try-me: try.txt'
GIT_MERGE_AUTOEDIT=no git flow feature finish try-me
git log --oneline -2 develop
bash ~/reset-git-lab.sh
```

## When something goes wrong

Every movement of `HEAD` is in `git reflog`, and `git reset --hard HEAD@{n}` goes back to any of
them: a commit no branch points at is still there until garbage collection. And `~/reset-git-lab.sh`
rebuilds the whole lab from scratch.

## Tests

```sh
vmctl group test git-lab        # nine scripts, one per exercise, over SSH
```
