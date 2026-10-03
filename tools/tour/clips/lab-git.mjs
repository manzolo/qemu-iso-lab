export const title = { en: "Git from underneath: the Git lab", it: "Git visto da sotto: il lab Git" };
export const series = "labs";
export const lab = "git-lab";

const LAB = "git-lab";
const VM = "git-lab-server";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";

export const cues = [
  { id: "intro", en: "Git is easier once you have seen what it stores. The Git lab is one server with a repository already made: real branches, a merge history, a resolved conflict, a remote. We read it from underneath, then practise on it.",
    it: "Git è più facile dopo aver visto cosa conserva. Il lab Git è un server con un repository già pronto: rami veri, una storia con merge, un conflitto risolto, un remote. Lo leggiamo da sotto, poi ci esercitiamo." },
  { id: "install", en: "One cloud image; the install builds the repository for your user and a bare remote beside it.",
    it: "Una cloud image; l'installazione costruisce il repository per il tuo utente e un remote bare accanto." },
  { id: "anatomy", en: "A repository is a .git directory: objects, refs, HEAD, config. The bare remote has exactly the same files and no working tree: that is all a Git server is.",
    it: "Un repository è una directory .git: objects, refs, HEAD, config. Il remote bare ha esattamente gli stessi file e nessun albero di lavoro: un server Git non è altro che questo." },
  { id: "objects", en: "The history as a graph. Then cat-file opens a commit: a tree, two parents, an author, a message. Two parents is what makes it a merge: nothing else marks it.",
    it: "La storia come grafo. Poi cat-file apre un commit: un tree, due genitori, un autore, un messaggio. Due genitori è ciò che lo rende un merge: nient'altro lo contrassegna." },
  { id: "treeblob", en: "The tree is a directory listing: mode, type, hash, name. The blob is the file's bytes and nothing else: no name, no history. The name lives in the tree.",
    it: "Il tree è un elenco di directory: modo, tipo, hash, nome. Il blob sono i byte del file e nient'altro: niente nome, niente storia. Il nome vive nel tree." },
  { id: "branches", en: "A branch is a file with one hash in it. HEAD is a file naming the branch you are on. Creating a branch copies nothing; that is why it is instant.",
    it: "Un ramo è un file con dentro un hash. HEAD è un file che nomina il ramo su cui sei. Creare un ramo non copia niente: ecco perché è istantaneo." },
  { id: "merge", en: "Merges: when both sides moved, Git writes a commit with two parents. feature/hello was merged like that; show prints the two parents.",
    it: "I merge: quando entrambi i lati si sono mossi, Git scrive un commit con due genitori. feature/hello è stato unito così; show stampa i due genitori." },
  { id: "conflict", en: "Now a conflict, made on purpose: two branches change the same line. The merge stops and Git writes both versions between markers.",
    it: "Ora un conflitto, fatto apposta: due rami cambiano la stessa riga. Il merge si ferma e Git scrive le due versioni fra i marcatori." },
  { id: "resolve", en: "You decide the content, add it, commit: the merge commit records the resolution. Then back to where we were.",
    it: "Decidi tu il contenuto, add, commit: il commit di merge registra la risoluzione. Poi si torna dov'eravamo." },
  { id: "stash", en: "Stash shelves a change you are not ready to commit: the tree is clean, the change waits on a stack, pop brings it back.",
    it: "Lo stash mette da parte una modifica che non sei pronto a committare: l'albero è pulito, la modifica aspetta su una pila, pop la riporta." },
  { id: "remote", en: "The remote. fetch updates your copy of its branches; these two ranges answer the only questions that matter: what is there and not here, what is here and not there.",
    it: "Il remote. fetch aggiorna la tua copia dei suoi rami; questi due intervalli rispondono alle sole domande che contano: cosa c'è di là e non di qua, cosa c'è di qua e non di là." },
  { id: "rebase", en: "Rebase replays commits on top of another branch: feature/stash sits on main's hotfix in a straight line, no merge commit. New hashes: never rebase what others already have.",
    it: "Il rebase riapplica i commit sopra un altro ramo: feature/stash sta sull'hotfix di main in linea retta, senza commit di merge. Hash nuovi: mai fare rebase di ciò che altri hanno già." },
  { id: "gitflow", en: "Git flow gives branches roles: develop integrates, features finish into it, a release finishes into main and leaves a tag. The lab's release 1.0 is tagged v1.0.",
    it: "Git flow dà ruoli ai rami: develop integra, le feature rientrano lì, una release rientra in main e lascia un tag. La release 1.0 del lab è taggata v1.0." },
  { id: "reset", en: "The exercises change history on purpose. One script rebuilds the whole repository in seconds.",
    it: "Gli esercizi cambiano la storia apposta. Uno script ricostruisce tutto il repository in pochi secondi." },
  { id: "tests", en: "Nine tests, one per exercise, check the repository is as the lab hands it to you.",
    it: "Nove test, uno per esercizio, controllano che il repository sia come il lab te lo consegna." },
  { id: "end", top: true, en: "Every command is in the guide, in English and Italian, from the lab's card.",
    it: "Ogni comando è nella guida, in inglese e in italiano, dalla card del lab." },
];

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl stop ${VM} >/dev/null 2>&1; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab");
  await d.sleep(3500);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(5000);
  d.ff(12);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(1500);

  await d.cue("anatomy");
  await d.run("clear");
  await d.session(VM);
  await d.guest("cd ~/workspace && ls -a .git", { read: 3000 });
  await d.guest("ls /srv/git/lab.git    # the bare remote: the same files, no working tree", { read: 4000 });

  await d.cue("objects");
  await d.guest("clear; git log --oneline --graph --all | head -20", { read: 5000 });
  await d.guest("git cat-file -p HEAD    # the commit object", { read: 5000 });
  await d.cue("treeblob");
  await d.guest("git cat-file -p HEAD^{tree}    # the tree: a directory listing", { read: 4500 });
  await d.guest("git cat-file -p HEAD:hello.txt    # the blob: bytes, no name", { read: 4000 });

  await d.cue("branches");
  await d.guest("clear; cat .git/HEAD; cat .git/refs/heads/main", { read: 4000 });
  await d.guest("git branch -a", { read: 4000 });

  await d.cue("merge");
  await d.guest("git log --merges --oneline", { read: 3500 });
  await d.guest("git show --stat --format='%h %s%nMerge: %p' HEAD~3 | head -4", { read: 4500 });

  await d.cue("conflict");
  await d.guest("clear; git switch -c branch-a && echo 'Version A' > clash.txt && git add clash.txt && git commit -q -m 'branch-a: clash.txt'", { read: 2000 });
  await d.guest("git switch main && echo 'Version B' > clash.txt && git add clash.txt && git commit -q -m 'main: clash.txt'", { read: 2000 });
  await d.guest("git merge branch-a; cat clash.txt    # the markers", { read: 5000 });
  await d.cue("resolve");
  await d.guest("echo 'Resolved version' > clash.txt && git add clash.txt && git commit -q -m 'Merge: resolve clash.txt' && git log --oneline --graph -4", { read: 4500 });
  await d.guest("git reset -q --hard origin/main && git branch -D branch-a    # back as it was", { read: 2500 });

  await d.cue("stash");
  await d.guest("clear; git switch feature/stash && echo 'More work in progress' >> wip.txt && git stash push -m 'WIP: my in-progress work'", { read: 2500 });
  await d.guest("git status --short; cat wip.txt; git stash list", { read: 4000 });
  await d.guest("git stash pop && cat wip.txt", { read: 3500 });
  await d.guest("git checkout -- wip.txt && git switch main", { read: 1500 });

  await d.cue("remote");
  await d.guest("clear; git remote -v", { read: 2500 });
  await d.guest("git fetch origin && git log --oneline HEAD..origin/main    # there, not here (nothing)", { read: 3000 });
  await d.guest("echo pushed > pushed.txt && git add pushed.txt && git commit -q -m 'Add pushed.txt' && git log --oneline origin/main..HEAD    # here, not pushed yet", { read: 4000 });
  await d.guest("git reset -q --hard HEAD~1    # undo", { read: 1500 });

  await d.cue("rebase");
  await d.guest("clear; git log --oneline --graph main feature/stash | head -8", { read: 5000 });
  await d.guest("git log --merges main..feature/stash --oneline | wc -l    # 0: linear", { read: 3500 });

  await d.cue("gitflow");
  await d.guest("clear; git flow version; git tag", { read: 3000 });
  await d.guest("git log --oneline --merges main", { read: 3500 });
  await d.guest("git log --oneline --graph develop -6", { read: 4500 });

  await d.cue("reset");
  await d.guest("bash ~/reset-git-lab.sh", { read: 3000, timeout: 120000 });
  await d.leave();

  await d.cue("tests");
  await d.run("clear");
  const before2 = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group test ${LAB}`, { wait: false });
  await d.sleep(6000);
  d.ff(6);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before2; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(4000);
  await d.cue("end");
  await d.sleep(2000);
}
