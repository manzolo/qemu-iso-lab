export const title = { en: "Git for beginners: your first repository", it: "Git per chi comincia: il primo repository" };
export const series = "labs";
export const lab = "git-lab";
export const order = 0;  // before "Git from underneath" on the tour page and the lab's card

const LAB = "git-lab";
const VM = "git-lab-server";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";

export const cues = [
  { id: "intro", en: "Never used Git? This lesson starts from nothing: a repository you make yourself, the first commits, a change, an undo, a branch, a remote. Ten minutes of the moves you will use every day.",
    it: "Non hai mai usato Git? Questa lezione parte da zero: un repository che fai tu, i primi commit, una modifica, un ripensamento, un ramo, un remote. Dieci minuti dei gesti che userai ogni giorno." },
  { id: "install", en: "The Git lab is one small server. Install it, then open a shell on it.",
    it: "Il lab Git è un piccolo server. Lo installiamo, poi apriamo una shell sopra." },
  { id: "who", en: "Git writes a name and an e-mail into every commit. The lab has set them already; elsewhere, git config does it once.",
    it: "Git scrive nome ed email dentro ogni commit. Nel lab sono già impostati; altrove li si imposta una volta con git config." },
  { id: "init", en: "A repository from nothing: git init creates a hidden .git directory. git status is the command to repeat whenever in doubt: it tells the branch and the state of every file.",
    it: "Un repository dal nulla: git init crea una directory nascosta, punto git. git status è il comando da ripetere a ogni dubbio: dice il ramo e lo stato di ogni file." },
  { id: "add", en: "A new file is untracked. git add puts it in the index, the list of what goes into the next commit; git commit takes the snapshot, with a message that says why.",
    it: "Un file nuovo è untracked. git add lo mette nell'indice, la lista di ciò che entrerà nel prossimo commit; git commit scatta l'istantanea, con un messaggio che dice perché." },
  { id: "diff", en: "Change the file: git diff shows line by line what differs from the last commit. Add and commit again; the log is the history in short.",
    it: "Modifichiamo il file: git diff mostra riga per riga cosa è diverso dall'ultimo commit. Di nuovo add e commit; il log è la storia in breve." },
  { id: "undo", en: "An undo: git restore throws away a change you have not committed. With --staged it only takes the file out of the index. And commit -a adds every tracked file that changed.",
    it: "Un ripensamento: git restore butta via una modifica non ancora committata. Con l'opzione staged toglie soltanto il file dall'indice. E commit con l'opzione a aggiunge da solo ogni file tracciato che è cambiato." },
  { id: "branch", en: "A branch is a separate line of work. Try an idea on it, and main does not move.",
    it: "Un ramo è una linea di lavoro separata. Ci provi un'idea, e main non si muove." },
  { id: "merge", en: "When it works, merge it into main. Main had not moved, so Git just moves it ahead: a fast-forward. The branch can go.",
    it: "Quando funziona, la unisci a main. Main non si era mosso, quindi Git lo sposta soltanto in avanti: un fast-forward. Il ramo può andare." },
  { id: "ignore", en: "Temporary files, builds, secrets: a .gitignore makes them disappear from status, and add skips them.",
    it: "File temporanei, build, segreti: un file punto gitignore li fa sparire da status, e add li salta." },
  { id: "remote", en: "A remote. A Git server is just a bare repository, here a directory: on GitHub only the address changes. remote add names it, push sends the branch.",
    it: "Un remote. Un server Git è soltanto un repository bare, qui una directory: su GitHub cambia solo l'indirizzo. remote add gli dà un nome, push manda il ramo." },
  { id: "clone", en: "clone makes a complete copy elsewhere, like a colleague would. One more commit, pushed; pull brings it into the copy.",
    it: "clone ne fa una copia completa altrove, come farebbe un collega. Un altro commit, pushato; pull lo porta nella copia." },
  { id: "end", top: true, en: "That is the daily loop: status, add, commit, push, pull. The next lesson opens Git from underneath, on the lab's own repository; the guide has every command, in English and Italian.",
    it: "Questo è il giro di ogni giorno: status, add, commit, push, pull. La prossima lezione apre Git da sotto, sul repository del lab; la guida ha ogni comando, in inglese e in italiano." },
];

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl stop ${VM} >/dev/null 2>&1; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

// Rewritten 2026-10-07 in the approved style (docs/TOUR.md, "How a lesson types its commands"):
// one short command per step, no comments typed on screen (the voice says them).
const say = (d, cmd, read = 3500) => d.guest(cmd, { read });

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.sleep(4000);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(5000);
  d.ff(12);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(1500);
  await d.clearScreen();
  await d.session(VM);

  await d.cue("who");
  await say(d, "git --version", 2500);
  await say(d, "git config --global --list", 4500);

  await d.cue("init");
  await d.clearScreen();
  await say(d, "mkdir ~/first-repo", 500);
  await say(d, "cd ~/first-repo", 500);
  await say(d, "git init -b main", 3000);
  await say(d, "git status", 4000);

  await d.cue("add");
  await d.clearScreen();
  await say(d, "echo 'Shopping list' > list.txt", 800);
  await say(d, "git status", 4000);
  await say(d, "git add list.txt", 800);
  await say(d, "git status", 4000);
  await say(d, "git commit -m 'Start the shopping list'", 2500);
  await say(d, "git log --oneline", 3500);

  await d.cue("diff");
  await d.clearScreen();
  await say(d, "echo 'milk' >> list.txt", 500);
  await say(d, "echo 'bread' >> list.txt", 500);
  await say(d, "git diff", 5000);
  await say(d, "git add list.txt", 500);
  await say(d, "git commit -m 'Add milk and bread'", 2500);
  await say(d, "git log --oneline", 3500);

  await d.cue("undo");
  await d.clearScreen();
  await say(d, "echo 'oops' >> list.txt", 500);
  await say(d, "git diff", 3500);
  await say(d, "git restore list.txt", 800);
  await say(d, "git diff", 2500);
  await say(d, "echo 'eggs' >> list.txt", 500);
  await say(d, "git add list.txt", 500);
  await say(d, "git restore --staged list.txt", 800);
  await say(d, "git status --short", 3500);
  await say(d, "git commit -am 'Add eggs'", 3000);

  await d.cue("branch");
  await d.clearScreen();
  await say(d, "git switch -c weekend", 2500);
  await say(d, "echo 'cake' >> list.txt", 500);
  await say(d, "git commit -am 'Weekend: cake'", 2500);
  await say(d, "git switch main", 1500);
  await say(d, "cat list.txt", 4000);

  await d.cue("merge");
  await d.clearScreen();
  await say(d, "git merge weekend", 3000);
  await say(d, "cat list.txt", 3000);
  await say(d, "git log --oneline --graph", 4500);
  await say(d, "git branch -d weekend", 2500);

  await d.cue("ignore");
  await d.clearScreen();
  await say(d, "echo 'scratch' > notes.tmp", 500);
  await say(d, "git status --short", 3000);
  await say(d, "echo '*.tmp' > .gitignore", 500);
  await say(d, "git status --short", 4500);
  await say(d, "git add .gitignore", 500);
  await say(d, "git commit -m 'Ignore temporary files'", 3000);

  await d.cue("remote");
  await d.clearScreen();
  await say(d, "git init --bare ~/first-remote.git", 3000);
  await say(d, "git remote add origin ~/first-remote.git", 800);
  await say(d, "git push -u origin main", 5000);

  await d.cue("clone");
  await d.clearScreen();
  await say(d, "git clone ~/first-remote.git ~/first-clone", 2500);
  await say(d, "git -C ~/first-clone log --oneline", 4000);
  await say(d, "echo 'coffee' >> list.txt", 500);
  await say(d, "git commit -am 'Add coffee'", 2000);
  await say(d, "git push", 3500);
  await say(d, "git -C ~/first-clone pull", 3000);
  await say(d, "cat ~/first-clone/list.txt", 5000);
  await d.leave();

  await d.cue("end");
  await d.sleep(6000);
}
