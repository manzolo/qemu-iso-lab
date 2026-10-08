// Git course, episode 4 of 6: stash, and a remote to share the work.
import { courseSetup, enter, say, AFTER_EPISODE_3 } from "../git-course.mjs";

export const title = { en: "Git 4/6 · Stash and remotes", it: "Git 4/6 · Stash e remote" };
export const series = "courses";
export const lab = "git-lab";
export const order = 4;

export const cues = [
  { id: "intro", en: "Episode four: putting work aside with stash, then a remote, to share the repository with a colleague.",
    it: "Quarta puntata: mettere da parte il lavoro con lo stash, poi un remote, per condividere il repository con un collega." },
  { id: "wip", en: "Work in progress: a line in the list, not committed. And something urgent arrives, which has nothing to do with it.",
    it: "Lavoro in corso: una riga nella lista, non committata. E arriva una cosa urgente, che non c'entra niente." },
  { id: "stash", en: "git stash puts the uncommitted changes on a shelf: the working copy is clean again, and stash list shows the shelf.",
    it: "git stash mette le modifiche non committate su uno scaffale: la copia di lavoro torna pulita, e stash list mostra lo scaffale." },
  { id: "urgent", en: "The urgent change, committed on its own.",
    it: "La modifica urgente, committata da sola." },
  { id: "pop", en: "stash pop takes the work back off the shelf, and the stash is empty again. Now it can be committed too.",
    it: "stash pop riprende il lavoro dallo scaffale, e lo stash torna vuoto. Ora si può committare anche quello." },
  { id: "bare", en: "Now a remote. A Git server is just a bare repository: the history without a working copy. Here it is a directory; on GitHub or GitLab only the address changes.",
    it: "Ora un remote. Un server Git è soltanto un repository bare: la storia senza una copia di lavoro. Qui è una directory; su GitHub o GitLab cambia solo l'indirizzo." },
  { id: "push", en: "remote add gives it the name origin. push -u sends main and ties our branch to it. branch -a now shows remotes/origin/main too: our record of where the remote's main is.",
    it: "remote add gli dà il nome origin. push con l'opzione u manda main e ci lega il nostro ramo. branch con l'opzione a ora mostra anche remotes/origin/main: il nostro ricordo di dov'è il main del remote." },
  { id: "clone", en: "A colleague clones the remote: a complete copy, history included. They add a line and push it.",
    it: "Un collega clona il remote: una copia completa, storia compresa. Aggiunge una riga e la pusha." },
  { id: "fetch", en: "Back in our repository. fetch downloads what is new without touching our branch: status says we are one commit behind, and the log shows which one.",
    it: "Torniamo nel nostro repository. fetch scarica le novità senza toccare il nostro ramo: status dice che siamo indietro di un commit, e il log mostra quale." },
  { id: "pull", en: "pull is fetch plus merge: the colleague's line is in our list.",
    it: "pull è fetch più merge: la riga del collega è nella nostra lista." },
  { id: "end", top: true, en: "Next episode: rebase, tags, and how to get back a commit you thought lost.",
    it: "Nella prossima puntata: rebase, tag, e come recuperare un commit che credevi perso." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_3);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(2000);
  await enter(d);
  await say(d, "git log --oneline", 5000);

  await d.cue("wip");
  await d.clearScreen();
  await say(d, "echo 'butter' >> list.txt", 800);
  await say(d, "git status --short", 5000);
  await d.cue("stash");
  await say(d, "git stash", 3000);
  await say(d, "git status --short", 2500);
  await say(d, "git stash list", 5000);

  await d.cue("urgent");
  await d.clearScreen();
  await say(d, "echo 'Water' >> drinks.txt", 800);
  await say(d, "git commit -am 'Add water'", 4000);

  await d.cue("pop");
  await say(d, "git stash pop", 5000);
  await say(d, "git stash list", 2500);
  await say(d, "git commit -am 'Add butter'", 4000);

  await d.cue("bare");
  await d.clearScreen();
  await say(d, "git init --bare ~/first-remote.git", 3500);
  await say(d, "ls ~/first-remote.git", 6000);

  await d.cue("push");
  await d.clearScreen();
  await say(d, "git remote add origin ~/first-remote.git", 1000);
  await say(d, "git remote -v", 4000);
  await say(d, "git push -u origin main", 6000);
  await say(d, "git branch -a", 6000);

  await d.cue("clone");
  await d.clearScreen();
  await say(d, "git clone ~/first-remote.git ~/colleague", 3000);
  await say(d, "cd ~/colleague", 800);
  await say(d, "git log --oneline -3", 4000);
  await say(d, "echo 'jam' >> list.txt", 800);
  await say(d, "git commit -am 'Add jam'", 2000);
  await say(d, "git push", 5000);

  await d.cue("fetch");
  await d.clearScreen();
  await say(d, "cd ~/first-repo", 800);
  await say(d, "git fetch", 3000);
  await say(d, "git status", 6000);
  await say(d, "git log --oneline main..origin/main", 5000);

  await d.cue("pull");
  await d.clearScreen();
  await say(d, "git pull", 5000);
  await say(d, "cat list.txt", 6000);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
