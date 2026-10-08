// Git course, episode 1 of 6: the first repository (tools/tour/git-course.mjs).
import { courseSetup, enter, say } from "../git-course.mjs";

export const title = { en: "Git 1/6 · The first repository", it: "Git 1/6 · Il primo repository" };
export const series = "courses";
export const lab = "git-lab";
export const order = 1;

export const cues = [
  { id: "intro", en: "A Git course in six episodes, slowly, on the Git lab: one small server. One repository from the first command to the last: a shopping list. This first episode makes it, and takes the first snapshots.",
    it: "Un corso su Git in sei puntate, con calma, sul lab Git: un piccolo server. Un solo repository dal primo comando all'ultimo: una lista della spesa. Questa prima puntata lo crea, e scatta le prime istantanee." },
  { id: "who", en: "Git writes a name and an e-mail into every commit. In the lab they are set already; on your machine, git config does it once.",
    it: "Git scrive un nome e un'email dentro ogni commit. Nel lab sono già impostati; sul tuo computer li imposti una volta con git config." },
  { id: "init", en: "A directory, and git init: the repository is born. All of Git lives in the hidden .git directory. git status is the command to repeat at every doubt: the branch, main, and nothing to commit yet.",
    it: "Una directory, e git init: il repository è nato. Tutto Git vive nella directory nascosta punto git. git status è il comando da ripetere a ogni dubbio: il ramo, main, e ancora niente da committare." },
  { id: "untracked", en: "A first file. Git sees it, but does not follow it yet: it is untracked.",
    it: "Un primo file. Git lo vede, ma non lo segue ancora: è untracked." },
  { id: "index", en: "git add puts it in the index, the list of what goes into the next commit. Status says it: changes to be committed.",
    it: "git add lo mette nell'indice, la lista di ciò che entrerà nel prossimo commit. Lo dice status: modifiche pronte per il commit." },
  { id: "commit", en: "git commit takes the snapshot, with a message that says why. The log shows it: a long hash that names it, the author, the date and the message.",
    it: "git commit scatta l'istantanea, con un messaggio che dice perché. Il log la mostra: un hash lungo che le dà un nome, l'autore, la data e il messaggio." },
  { id: "diff", en: "Now change the file. Status in short form marks it modified, and git diff shows line by line what differs from the last commit: the plus signs are the new lines.",
    it: "Ora modifichiamo il file. Status in forma breve lo segna modificato, e git diff mostra riga per riga cosa è diverso dall'ultimo commit: i più sono le righe nuove." },
  { id: "staged", en: "After git add, plain diff is empty: the change has moved into the index. diff --staged shows what the commit will contain.",
    it: "Dopo git add, il diff semplice è vuoto: la modifica è passata nell'indice. diff con l'opzione staged mostra cosa conterrà il commit." },
  { id: "second", en: "Second commit. The short log has one line per commit, the newest first; git show opens one of them, change included.",
    it: "Secondo commit. Il log breve ha una riga per commit, il più recente in alto; git show ne apre uno, con la modifica dentro." },
  { id: "restore", en: "An undo. A wrong line, not yet added: git restore throws it away and the file is back as in the last commit.",
    it: "Un ripensamento. Una riga sbagliata, non ancora aggiunta: git restore la butta via e il file torna com'era nell'ultimo commit." },
  { id: "unstage", en: "A line added to the index too early: restore --staged takes it out of the index, and the change stays in the file. commit -a then adds every tracked file that changed, by itself.",
    it: "Una riga aggiunta all'indice troppo presto: restore con l'opzione staged la toglie dall'indice, e la modifica resta nel file. Poi commit con l'opzione a aggiunge da solo ogni file tracciato che è cambiato." },
  { id: "ignore", en: "Temporary files, builds, secrets: Git must not track them. A .gitignore lists them, and status stops showing them. The .gitignore itself is committed, for everyone.",
    it: "File temporanei, build, segreti: Git non li deve seguire. Un punto gitignore li elenca, e status smette di mostrarli. Il punto gitignore stesso si committa, per tutti." },
  { id: "log", en: "Four snapshots, four commits: the history of the list.",
    it: "Quattro istantanee, quattro commit: la storia della lista." },
  { id: "end", top: true, en: "Next episode: inside the .git directory, what a commit really is.",
    it: "Nella prossima puntata: dentro la directory punto git, cos'è davvero un commit." },
];

export async function setup(d) {
  await courseSetup(d);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(2000);
  await enter(d, { repo: false });

  await d.cue("who");
  await say(d, "git --version", 2500);
  await say(d, "git config --global --list", 6000);

  await d.cue("init");
  await d.clearScreen();
  await say(d, "mkdir ~/first-repo", 600);
  await say(d, "cd ~/first-repo", 600);
  await say(d, "git init -b main", 3000);
  await say(d, "ls -a", 3500);
  await say(d, "git status", 6000);

  await d.cue("untracked");
  await d.clearScreen();
  await say(d, "echo 'Shopping list' > list.txt", 1000);
  await say(d, "git status", 7000);
  await d.cue("index");
  await say(d, "git add list.txt", 1000);
  await say(d, "git status", 7000);

  await d.cue("commit");
  await d.clearScreen();
  await say(d, "git commit -m 'Start the shopping list'", 3500);
  await say(d, "git log", 8000);

  await d.cue("diff");
  await d.clearScreen();
  await say(d, "echo 'milk' >> list.txt", 600);
  await say(d, "echo 'bread' >> list.txt", 600);
  await say(d, "git status --short", 3500);
  await say(d, "git diff", 7000);
  await d.cue("staged");
  await say(d, "git add list.txt", 800);
  await say(d, "git diff", 2500);
  await say(d, "git diff --staged", 7000);

  await d.cue("second");
  await d.clearScreen();
  await say(d, "git commit -m 'Add milk and bread'", 3000);
  await say(d, "git log --oneline", 4500);
  await say(d, "git show", 8000);

  await d.cue("restore");
  await d.clearScreen();
  await say(d, "echo 'oops' >> list.txt", 800);
  await say(d, "git diff", 5000);
  await say(d, "git restore list.txt", 1000);
  await say(d, "cat list.txt", 5000);

  await d.cue("unstage");
  await d.clearScreen();
  await say(d, "echo 'eggs' >> list.txt", 800);
  await say(d, "git add list.txt", 800);
  await say(d, "git restore --staged list.txt", 1000);
  await say(d, "git status --short", 5000);
  await say(d, "git commit -am 'Add eggs'", 5000);

  await d.cue("ignore");
  await d.clearScreen();
  await say(d, "echo 'scratch' > notes.tmp", 800);
  await say(d, "git status --short", 4000);
  await say(d, "echo '*.tmp' > .gitignore", 800);
  await say(d, "git status --short", 5000);
  await say(d, "git add .gitignore", 800);
  await say(d, "git commit -m 'Ignore temporary files'", 4000);

  await d.cue("log");
  await d.clearScreen();
  await say(d, "git log --oneline", 6000);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
