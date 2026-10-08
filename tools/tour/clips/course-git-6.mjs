// Git course, episode 6 of 6: git flow, branches with roles (tools/tour/git-course.mjs).
import { courseSetup, enter, say, AFTER_EPISODE_5 } from "../git-course.mjs";

export const title = { en: "Git 6/6 · Git flow", it: "Git 6/6 · Git flow" };
export const series = "courses";
export const lab = "git-lab";
export const order = 6;

export const cues = [
  { id: "intro", en: "Last episode: git flow. A way to organise the branches of a project, where every branch has a role, and a tool that does the branching, merging and tagging for you.",
    it: "Ultima puntata: git flow. Un modo di organizzare i rami di un progetto, dove ogni ramo ha un ruolo, e uno strumento che crea, unisce ed etichetta i rami al posto tuo." },
  { id: "roles", en: "The roles: main holds only the released versions; develop is where the work comes together; feature branches start from develop, and release and hotfix branches lead to main.",
    it: "I ruoli: main contiene solo le versioni rilasciate; develop è dove il lavoro si riunisce; i rami feature partono da develop, e i rami release e hotfix portano a main." },
  { id: "init", en: "git flow init with the defaults creates develop, and writes the names of the roles into the repository's configuration. One more setting: version tags start with a v, like the v1.0 of episode five.",
    it: "git flow init con le scelte predefinite crea develop, e scrive i nomi dei ruoli nella configurazione del repository. Un'impostazione in più: le etichette delle versioni cominciano con una v, come la v1.0 della quinta puntata." },
  { id: "feature", en: "A feature: feature start makes a branch from develop and moves onto it. One commit, a new recipe.",
    it: "Una feature: feature start crea un ramo da develop e ci si sposta. Un commit, una ricetta nuova." },
  { id: "feature-finish", en: "feature finish merges it into develop, deletes the branch, and leaves us on develop. Main has not moved: the dessert is not released yet.",
    it: "feature finish la unisce a develop, cancella il ramo, e ci lascia su develop. Main non si è mosso: il dolce non è ancora rilasciato." },
  { id: "release", en: "A release: release start one point one, a branch where the version is prepared. Here, a VERSION file.",
    it: "Una release: release start uno punto uno, un ramo dove si prepara la versione. Qui, un file VERSION." },
  { id: "release-finish", en: "release finish does four things: merges into main, tags v1.1, merges back into develop, and deletes the branch. git tag shows the new version.",
    it: "release finish fa quattro cose: unisce a main, mette l'etichetta v1.1, riunisce a develop, e cancella il ramo. git tag mostra la versione nuova." },
  { id: "hotfix", en: "And an urgent fix on a released version: a hotfix starts from main, not from develop.",
    it: "E una correzione urgente su una versione già rilasciata: un hotfix parte da main, non da develop." },
  { id: "hotfix-finish", en: "hotfix finish brings it to main with the tag v1.1.1, and to develop too, so the fix is not lost at the next release.",
    it: "hotfix finish la porta su main con l'etichetta v1.1.1, e anche su develop, così la correzione non si perde alla prossima release." },
  { id: "graph", en: "The graph shows the two lines: main with one merge per version, develop with everything. Push both, and the tags.",
    it: "Il grafo mostra le due linee: main con un merge per ogni versione, develop con tutto. Pushiamo entrambi, e le etichette." },
  { id: "recap", en: "Six episodes: commits, the objects inside .git, branches and conflicts, stash and remotes, rebase, tags and reflog, and git flow. The whole history of the list is here.",
    it: "Sei puntate: i commit, gli oggetti dentro punto git, rami e conflitti, stash e remote, rebase, tag e reflog, e git flow. Tutta la storia della lista è qui." },
  { id: "end", top: true, en: "The Git lab's guide has every command of the course, in English and Italian, from the lab's card.",
    it: "La guida del lab Git ha ogni comando del corso, in inglese e in italiano, dalla card del lab." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_5);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(2000);
  await enter(d);
  await say(d, "git log --oneline -3", 4000);
  await d.cue("roles");
  await say(d, "git branch", 9000);

  await d.cue("init");
  await d.clearScreen();
  await say(d, "git flow init -d", 4000);
  await say(d, "git branch", 3000);
  await say(d, "git config gitflow.prefix.versiontag v", 1000);
  await say(d, "git config --get-regexp gitflow", 7000);

  await d.cue("feature");
  await d.clearScreen();
  await say(d, "git flow feature start dessert", 5000);
  await say(d, "echo 'Tiramisu: mascarpone, coffee' >> recipes.txt", 800);
  await say(d, "git commit -am 'Add tiramisu'", 3000);
  await d.cue("feature-finish");
  await d.clearScreen();
  await say(d, "git flow feature finish dessert", 7000);
  await say(d, "git log --oneline --graph --all -5", 7000);

  await d.cue("release");
  await d.clearScreen();
  await say(d, "git flow release start 1.1", 5000);
  await say(d, "echo '1.1' > VERSION", 800);
  await say(d, "git add VERSION", 800);
  await say(d, "git commit -m 'Version 1.1'", 3000);
  await d.cue("release-finish");
  await d.clearScreen();
  await say(d, "git flow release finish -m 'Release 1.1' 1.1", 9000);
  await say(d, "git tag", 4000);

  await d.cue("hotfix");
  await d.clearScreen();
  await say(d, "git flow hotfix start 1.1.1", 5000);
  await say(d, "echo 'salt' >> list.txt", 800);
  await say(d, "git commit -am 'Forgot the salt'", 3000);
  await d.cue("hotfix-finish");
  await d.clearScreen();
  await say(d, "git flow hotfix finish -m 'Hotfix 1.1.1' 1.1.1", 9000);
  await say(d, "git tag", 4000);

  await d.cue("graph");
  await d.clearScreen();
  await say(d, "git log --oneline --graph --all -12", 10000);
  await say(d, "git push origin main develop --tags", 6000);

  await d.cue("recap");
  await d.clearScreen();
  await say(d, "cat list.txt", 5000);
  await say(d, "git log --oneline main -6", 8000);
  await d.leave();

  await d.cue("end");
  await d.sleep(6000);
}
