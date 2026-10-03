# Lab Git: commit, rami, merge, conflitti, stash, rebase e git flow

Un server con un repository già pronto, e Git guardato da sotto prima di esercitarlo: un commit è un
oggetto che si può leggere, un ramo è un file con dentro un hash, l'indice sta fra i tuoi file e la
storia. Portato dal git-lab di qlab.

| VM | Cosa contiene | SSH dall'host |
|---|---|---|
| `git-lab-server` | `~/workspace` (il repository di lavoro, su `main`) e `/srv/git/lab.git` (il suo remote bare, `origin`) | `vmctl shell git-lab-server` (127.0.0.1:2365) |

La storia del repository, costruita dall'installazione (`~/setup-git-lab.sh`): tre commit di base;
`feature/hello` unito in `main`; `feature/conflict` e un conflitto su `conflict.txt` risolto su `main`;
`feature/stash` con una dimostrazione di stash, poi riallineato con rebase su un hotfix di `main`;
`develop` con una feature git-flow conclusa e la release 1.0 unita in `main` e taggata `v1.0`. Gli
esercizi cambiano la storia apposta: **`bash ~/reset-git-lab.sh` ricostruisce tutto** in pochi secondi.

## Avvio

```sh
vmctl group install git-lab      # una cloud image, circa un minuto dopo il download
vmctl shell git-lab-server
cd ~/workspace
```

## Esercizio 1: anatomia di Git

Un repository è una directory `.git/`: `objects` (ogni versione di ogni file, e ogni commit), `refs`
(rami e tag), `HEAD` (dove sei) e `config`. Il repository bare è la stessa cosa senza un albero di
lavoro accanto: un server Git non è altro che questo.

```sh
ls -a .git
git config --global --list
git status; git branch
ls /srv/git/lab.git
```

## Esercizio 2: i commit sono oggetti

`git cat-file -p` stampa qualsiasi oggetto. Un commit contiene un tree, i genitori, autore, committer
e messaggio; il tree è un elenco di directory (modo, tipo, hash, nome); il blob è il contenuto e
nient'altro. Un commit di merge è semplicemente un commit con due genitori. Niente viene mai
modificato sul posto: `--amend` crea un commit nuovo con un hash nuovo e sposta il ramo lì.

```sh
git log --oneline --graph --all
git cat-file -p HEAD
git cat-file -p HEAD^{tree}
git cat-file -p HEAD:hello.txt
echo 'My notes' > notes.txt && git add notes.txt && git commit -m 'Add notes.txt'
git commit --amend -m 'docs: add notes.txt'
git reset --hard HEAD~1
```

## Esercizio 3: i rami sono puntatori

`cat .git/refs/heads/main` è tutto il ramo: 40 caratteri esadecimali. `HEAD` nomina il ramo su cui
sei; un "detached HEAD" contiene un hash al suo posto. I `remotes/origin/*` sono l'ultima copia che
hai dei rami del remote, aggiornata da `fetch`, non il remote stesso.

```sh
git branch -a
cat .git/HEAD; cat .git/refs/heads/main
git branch --merged main
git switch -c feature/my-feature && git switch main
git branch -d feature/my-feature
```

## Esercizio 4: merge, fast-forward o commit di merge

Se `main` non si è mosso da quando il ramo è nato, il merge sposta solo il puntatore in avanti. Se si
sono mossi entrambi, Git scrive un commit di merge con due genitori; `--no-ff` ne chiede uno anche
quando potrebbe fare fast-forward, per lasciare la feature visibile nel grafo.

```sh
git log --merges --oneline
git show --stat HEAD~3
git switch -c feature/test-merge && echo test > merge-test.txt && git add merge-test.txt && git commit -m 'test: add merge-test.txt'
git switch main && git merge --no-ff feature/test-merge -m 'Merge feature/test-merge'
git log --oneline --graph -4
git reset --hard origin/main && git branch -D feature/test-merge
```

## Esercizio 5: conflitti

Due rami hanno cambiato le stesse righe di `conflict.txt`. Git ha fermato il merge e scritto le due
versioni fra `<<<<<<<`, `=======` e `>>>>>>>`; il lab l'ha risolto e ha fatto commit. Fanne uno tuo:
un ramo, la stessa riga cambiata da due parti, merge, leggi i marcatori, scrivi il contenuto che vuoi,
add, commit.

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

## Esercizio 6: stash

`git stash` mette le modifiche non ancora committate su una pila e lascia l'albero di lavoro pulito,
così puoi cambiare ramo o fare pull; `git stash pop` riporta indietro la voce più recente. Gli stash
sono locali: non vanno mai su un remote. `-u` include i file non tracciati.

```sh
git switch feature/stash
echo 'More work in progress' >> wip.txt && git stash push -m 'WIP: my in-progress work'
git status --short; cat wip.txt
git stash list
git stash pop && cat wip.txt
git checkout -- wip.txt && git switch main
```

## Esercizio 7: remote

`origin` è il repository bare. `push` carica i tuoi commit e sposta il ramo del remote; `fetch` scarica
i ref del remote in `remotes/origin/*` senza toccare i tuoi rami; `pull` è fetch più merge. I due
intervalli qui sotto sono la domanda da farsi prima di entrambi: cosa c'è di là che non c'è di qua, e
viceversa.

```sh
git remote -v
git fetch origin && git log --oneline HEAD..origin/main
git log --oneline origin/main..HEAD
git ls-remote origin
echo pushed > pushed.txt && git add pushed.txt && git commit -m 'Add pushed.txt' && git push origin main
git reset --hard HEAD~1 && git push --force origin main
```

L'ultima riga riscrive il `main` del remote: va bene sul remote del tuo lab, è una pessima abitudine
ovunque ci sia qualcun altro.

## Esercizio 8: rebase

`rebase` riapplica i commit di un ramo sopra un altro ramo: il risultato è una linea retta senza
commit di merge, con hash nuovi. `feature/stash` è stato riallineato sull'hotfix di `main`. La regola:
mai fare rebase di commit che altri hanno già.

```sh
git log --oneline feature/stash
git log --merges main..feature/stash --oneline
git log --oneline --graph main feature/stash
git switch -c feature/rebase-demo main~2 && echo 'old base' > rebase-demo.txt && git add rebase-demo.txt && git commit -m 'demo commit'
git rebase main && git log --oneline -3
git switch main && git branch -D feature/rebase-demo
```

## Esercizio 9: git flow

Un modello di rami con dei ruoli: `main` tiene le release, `develop` integra, i `feature/*` partono da
`develop` e vi rientrano, i `release/*` rientrano sia in `main` sia in `develop` e lasciano un tag, gli
`hotfix/*` partono da `main`. La CLI `git-flow` fa i rami, i merge, i tag e la pulizia; le sue
impostazioni stanno in `.git/config`.

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

## Quando qualcosa va storto

Ogni spostamento di `HEAD` è in `git reflog`, e `git reset --hard HEAD@{n}` torna a uno qualsiasi
di quei punti: un commit che nessun ramo indica più c'è ancora, finché la garbage collection non lo
toglie. E `~/reset-git-lab.sh` ricostruisce tutto il lab da zero.

## Test

```sh
vmctl group test git-lab        # nove script, uno per esercizio, via SSH
```
