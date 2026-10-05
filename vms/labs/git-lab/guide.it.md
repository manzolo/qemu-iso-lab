# Lab Git: commit, rami, merge, conflitti, stash, rebase e git flow

Un server con un repository già pronto, e Git guardato da sotto prima di esercitarlo: un commit è un
oggetto che si può leggere, un ramo è un file con dentro un hash, l'indice sta fra i tuoi file e la
storia. Portato dal git-lab di qlab. **Chi comincia parte dall'esercizio 0**, un repository fatto da zero.

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

## Esercizio 0: i primi passi, per chi comincia

Se non hai mai usato Git, parti da qui: un repository fatto da zero in `~/first-repo`, lontano da
`~/workspace`, e i gesti di ogni giorno. Git conserva **istantanee** del tuo lavoro: ogni volta che
sei contento di come stanno le cose, ne fai una (un *commit*) con un messaggio che dice perché.
Prima controlla chi sei: Git scrive nome ed email dentro ogni commit (nel lab sono già impostati;
altrove si fanno con `git config --global user.name 'Il Tuo Nome'` e `user.email`).

```sh
git --version
git config --global --list
```

**Un repository dal nulla.** `git init` crea la directory nascosta `.git/`, dove Git terrà tutto.
`git status` è il comando da ripetere a ogni dubbio: dice su che ramo sei e in che stato è ogni
file. Un file nuovo è *untracked* finché non lo aggiungi; `git add` lo mette nell'*indice*, la
lista di ciò che entrerà nel prossimo commit; `git commit -m` scatta l'istantanea.

```sh
mkdir -p ~/first-repo && cd ~/first-repo && git init -b main
git status
echo 'Shopping list' > list.txt && git status
git add list.txt && git status
git commit -m 'Start the shopping list' && git log --oneline
```

**Cambiare, vedere il cambiamento, committare di nuovo.** `git diff` mostra riga per riga cosa è
diverso dall'ultimo commit (`+` aggiunta, `-` tolta). `git log --oneline` è la storia in breve,
`git show` apre un commit.

```sh
echo 'milk' >> list.txt && echo 'bread' >> list.txt
git diff
git add list.txt && git commit -m 'Add milk and bread'
git log --oneline && git show --stat HEAD
```

**Tornare indietro prima che sia tardi.** `git restore <file>` butta via le modifiche non ancora
committate di quel file; `git restore --staged` lo toglie dall'indice lasciando la modifica nella
copia di lavoro. `git commit -a` aggiunge da solo ogni file già tracciato che è cambiato.

```sh
echo 'oops' >> list.txt && git diff --stat
git restore list.txt && git diff --stat
echo 'eggs' >> list.txt && git add list.txt && git restore --staged list.txt && git status --short
git commit -am 'Add eggs'
```

**Un ramo per un'idea, unito quando funziona.** Un ramo è una linea di lavoro separata: ci provi
senza toccare `main`, e quando sei contento lo unisci con `merge`. Qui `main` non si è mosso nel
frattempo, quindi Git fa un *fast-forward*: sposta `main` in avanti e basta.

```sh
git switch -c weekend
echo 'cake' >> list.txt && git commit -am 'Weekend: cake'
git switch main && cat list.txt
git merge weekend && cat list.txt && git log --oneline --graph
git branch -d weekend
```

**Quello che Git non deve tracciare.** File temporanei, build, segreti: un `.gitignore` con i loro
nomi (o pattern) li fa sparire da `git status`, e `git add` li salta.

```sh
echo 'scratch' > notes.tmp && printf '*.tmp\n' > .gitignore && git status --short
git add .gitignore && git commit -m 'Ignore temporary files'
```

**Un remote: push, clone, pull.** Un server Git è un repository *bare* (senza copia di lavoro),
qui una directory; su GitHub o GitLab cambia solo l'indirizzo. `remote add` gli dà un nome,
`push -u` manda il ramo e lo collega, `clone` ne fa una copia completa altrove, `pull` prende i
commit nuovi.

```sh
git init --bare ~/first-remote.git
git remote add origin ~/first-remote.git && git push -u origin main
git clone ~/first-remote.git ~/first-clone && git -C ~/first-clone log --oneline
echo 'coffee' >> list.txt && git commit -am 'Add coffee' && git push
git -C ~/first-clone pull && cat ~/first-clone/list.txt
```

Per ricominciare da capo: `rm -rf ~/first-repo ~/first-remote.git ~/first-clone`. Poi, con questi
gesti nelle mani, gli esercizi che seguono aprono Git da sotto, su `~/workspace`.

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
