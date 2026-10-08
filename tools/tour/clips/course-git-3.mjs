// Git course, episode 3 of 6: branches, merges and a conflict.
import { courseSetup, enter, say, AFTER_EPISODE_1 } from "../git-course.mjs";

export const title = { en: "Git 3/6 · Branches, merges, conflicts", it: "Git 3/6 · Rami, merge, conflitti" };
export const series = "courses";
export const lab = "git-lab";
export const order = 3;

export const cues = [
  { id: "intro", en: "Episode three: branches. A branch is a separate line of work: you try an idea there, and main does not move until you decide.",
    it: "Terza puntata: i rami. Un ramo è una linea di lavoro separata: ci provi un'idea, e main non si muove finché non decidi tu." },
  { id: "branch", en: "git branch lists them: only main, with a star. switch -c creates a branch and moves onto it; now HEAD names weekend.",
    it: "git branch li elenca: solo main, con l'asterisco. switch con l'opzione c crea un ramo e ci si sposta; ora HEAD nomina weekend." },
  { id: "work", en: "A commit on weekend. The graph shows the two pointers: weekend one commit ahead, main where it was.",
    it: "Un commit su weekend. Il grafo mostra i due puntatori: weekend avanti di un commit, main dov'era." },
  { id: "ff", en: "Back on main, and merge. Main had not moved, so Git just moves it ahead to weekend: a fast-forward, no new commit.",
    it: "Torniamo su main, e merge. Main non si era mosso, quindi Git lo sposta soltanto in avanti fino a weekend: un fast-forward, nessun commit nuovo." },
  { id: "diverge", en: "Now two lines that both move. A party branch adds a line to the list; meanwhile main gets a new file, drinks. The graph forks.",
    it: "Ora due linee che si muovono entrambe. Un ramo party aggiunge una riga alla lista; intanto main riceve un file nuovo, drinks. Il grafo si biforca." },
  { id: "merge", en: "This merge cannot be a fast-forward: Git writes a merge commit, a commit with two parents, and the graph closes again.",
    it: "Questo merge non può essere un fast-forward: Git scrive un merge commit, un commit con due genitori, e il grafo si richiude." },
  { id: "setup-conflict", en: "And a conflict. The menu says pizza. A veggie branch changes the same line to salad; main changes it to pasta.",
    it: "E un conflitto. Il menù dice pizza. Un ramo veggie cambia la stessa riga in salad; main la cambia in pasta." },
  { id: "conflict", en: "The merge stops: CONFLICT in menu.txt. Status lists it under unmerged paths. In the file, Git wrote both versions: ours between the less-than signs and the equals, theirs down to the greater-than signs.",
    it: "Il merge si ferma: CONFLICT in menu.txt. Status lo elenca fra i percorsi non uniti. Nel file Git ha scritto entrambe le versioni: la nostra fra i segni di minore e gli uguali, la loro fino ai segni di maggiore." },
  { id: "resolve", en: "Resolving is writing the content you want, without the markers. add marks it resolved, and commit closes the merge, with the message Git prepared.",
    it: "Risolvere vuol dire scrivere il contenuto che vuoi, senza i segni. add lo segna risolto, e commit chiude il merge, col messaggio che Git ha preparato." },
  { id: "cleanup", en: "The graph tells the whole story. The merged branches can go: branch -d refuses one that is not merged yet.",
    it: "Il grafo racconta tutta la storia. I rami uniti possono andare: branch con l'opzione d rifiuta un ramo non ancora unito." },
  { id: "end", top: true, en: "Next episode: stash, and a remote to share the work with.",
    it: "Nella prossima puntata: lo stash, e un remote per condividere il lavoro." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_1);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(2000);
  await enter(d);

  await d.cue("branch");
  await d.clearScreen();
  await say(d, "git branch", 3500);
  await say(d, "git switch -c weekend", 3000);
  await say(d, "git branch", 3000);
  await say(d, "cat .git/HEAD", 4000);

  await d.cue("work");
  await d.clearScreen();
  await say(d, "echo 'cake' >> list.txt", 800);
  await say(d, "git commit -am 'Weekend: cake'", 2500);
  await say(d, "git log --oneline --graph --all", 7000);

  await d.cue("ff");
  await d.clearScreen();
  await say(d, "git switch main", 2000);
  await say(d, "git merge weekend", 5000);
  await say(d, "git log --oneline --graph --all", 6000);

  await d.cue("diverge");
  await d.clearScreen();
  await say(d, "git switch -c party", 1500);
  await say(d, "echo 'balloons' >> list.txt", 800);
  await say(d, "git commit -am 'Party: balloons'", 2000);
  await say(d, "git switch main", 1500);
  await say(d, "echo 'Tea' > drinks.txt", 800);
  await say(d, "git add drinks.txt", 800);
  await say(d, "git commit -m 'Add drinks'", 2000);
  await say(d, "git log --oneline --graph --all", 7000);

  await d.cue("merge");
  await d.clearScreen();
  await say(d, "git merge party --no-edit", 4000);
  await say(d, "git log --oneline --graph", 8000);

  await d.cue("setup-conflict");
  await d.clearScreen();
  await say(d, "echo 'Dinner: pizza' > menu.txt", 800);
  await say(d, "git add menu.txt", 800);
  await say(d, "git commit -m 'Plan dinner'", 1500);
  await say(d, "git switch -c veggie", 1500);
  await say(d, "echo 'Dinner: salad' > menu.txt", 800);
  await say(d, "git commit -am 'Veggie dinner'", 1500);
  await say(d, "git switch main", 1500);
  await say(d, "echo 'Dinner: pasta' > menu.txt", 800);
  await say(d, "git commit -am 'Pasta dinner'", 3000);

  await d.cue("conflict");
  await d.clearScreen();
  await say(d, "git merge veggie", 5000);
  await say(d, "git status", 7000);
  await say(d, "cat menu.txt", 9000);

  await d.cue("resolve");
  await d.clearScreen();
  await say(d, "echo 'Dinner: pasta salad' > menu.txt", 1000);
  await say(d, "git add menu.txt", 1000);
  await say(d, "git commit --no-edit", 3000);
  await say(d, "git status", 4000);

  await d.cue("cleanup");
  await d.clearScreen();
  await say(d, "git log --oneline --graph", 9000);
  await say(d, "git branch -d weekend party veggie", 3500);
  await say(d, "git branch", 4000);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
