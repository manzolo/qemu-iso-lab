// Git course, episode 5 of 6: rebase, tags, and the reflog as a safety net.
import { courseSetup, enter, say, AFTER_EPISODE_4 } from "../git-course.mjs";

export const title = { en: "Git 5/6 · Rebase, tags, reflog", it: "Git 5/6 · Rebase, tag, reflog" };
export const series = "courses";
export const lab = "git-lab";
export const order = 5;

export const cues = [
  { id: "intro", en: "Episode five: rebase, an alternative to merge; tags, to name a release; and the reflog, to get back what seemed lost.",
    it: "Quinta puntata: il rebase, un'alternativa al merge; i tag, per dare un nome a una versione; e il reflog, per recuperare ciò che sembrava perso." },
  { id: "fork", en: "A recipes branch with its commit; meanwhile main moves too. The graph forks, as in episode three.",
    it: "Un ramo recipes con il suo commit; intanto si muove anche main. Il grafo si biforca, come nella terza puntata." },
  { id: "rebase", en: "This time, instead of a merge commit, rebase: Git replays the branch's commits on top of main. The graph is a straight line again.",
    it: "Questa volta, invece di un merge commit, il rebase: Git rigioca i commit del ramo sopra main. Il grafo torna una linea dritta." },
  { id: "hash", en: "The replayed commit has a new hash: it is a new commit. That is the rule: never rebase commits other people already have.",
    it: "Il commit rigiocato ha un hash nuovo: è un commit nuovo. Da qui la regola: mai fare rebase di commit che altri hanno già." },
  { id: "ff", en: "Now main can take it with a plain fast-forward.",
    it: "Ora main lo può prendere con un semplice fast-forward." },
  { id: "tag", en: "A tag gives a commit a name that never moves: version one point zero. With -a it is an annotated tag, with author, date and message. Tags go to the remote only when you push them.",
    it: "Un tag dà a un commit un nome che non si sposta mai: versione uno punto zero. Con l'opzione a è un tag annotato, con autore, data e messaggio. I tag vanno sul remote solo quando li pushi." },
  { id: "disaster", en: "And now a mistake: reset --hard two commits back. The log has lost them, and the files too.",
    it: "E ora un errore: reset con l'opzione hard di due commit indietro. Il log li ha persi, e anche i file." },
  { id: "rescue", en: "But the reflog remembers every position of HEAD: the line before the reset is where we were. reset --hard to it, and everything is back.",
    it: "Ma il reflog ricorda ogni posizione di HEAD: la riga prima del reset è dove eravamo. Un reset con l'opzione hard lì, e torna tutto." },
  { id: "end", top: true, en: "Last episode: git flow, a way to organise the branches of a project, with releases and hotfixes.",
    it: "Nell'ultima puntata: git flow, un modo di organizzare i rami di un progetto, con versioni e correzioni urgenti." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_4);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(2000);
  await enter(d);
  await say(d, "git log --oneline -4", 4000);

  await d.cue("fork");
  await d.clearScreen();
  await say(d, "git switch -c recipes", 1500);
  await say(d, "echo 'Pancakes: eggs, milk' > recipes.txt", 800);
  await say(d, "git add recipes.txt", 800);
  await say(d, "git commit -m 'Add recipes'", 2000);
  await say(d, "git switch main", 1500);
  await say(d, "echo 'Coffee' >> drinks.txt", 800);
  await say(d, "git commit -am 'Add coffee'", 2000);
  await say(d, "git log --oneline --graph --all -5", 8000);

  await d.cue("rebase");
  await d.clearScreen();
  await say(d, "git switch recipes", 1500);
  await say(d, "git rebase main", 4000);
  await say(d, "git log --oneline --graph --all -5", 8000);
  await d.cue("hash");
  await say(d, "git reflog -4", 8000);

  await d.cue("ff");
  await d.clearScreen();
  await say(d, "git switch main", 1500);
  await say(d, "git merge recipes", 5000);
  await say(d, "git branch -d recipes", 3000);

  await d.cue("tag");
  await d.clearScreen();
  await say(d, "git tag -a v1.0 -m 'First complete list'", 1500);
  await say(d, "git tag", 3000);
  await say(d, "git log --oneline --decorate -3", 6000);
  await say(d, "git push origin main v1.0", 6000);

  await d.cue("disaster");
  await d.clearScreen();
  await say(d, "git reset --hard HEAD~2", 3500);
  await say(d, "git log --oneline -3", 5000);
  await say(d, "ls", 4000);

  await d.cue("rescue");
  await d.clearScreen();
  await say(d, "git reflog -3", 8000);
  await say(d, "git reset --hard HEAD@{1}", 3500);
  await say(d, "git log --oneline -3", 4000);
  await say(d, "ls", 5000);

  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
