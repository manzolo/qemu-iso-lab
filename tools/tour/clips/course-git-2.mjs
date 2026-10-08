// Git course, episode 2 of 6: inside .git, commits are objects and branches are pointers.
import { courseSetup, enter, say, AFTER_EPISODE_1 } from "../git-course.mjs";

export const title = { en: "Git 2/6 · Inside .git", it: "Git 2/6 · Dentro .git" };
export const series = "courses";
export const lab = "git-lab";
export const order = 2;

export const cues = [
  { id: "intro", en: "Episode two: inside the .git directory. Same repository as the first episode, four commits. Today we open them.",
    it: "Seconda puntata: dentro la directory punto git. Lo stesso repository della prima puntata, quattro commit. Oggi li apriamo." },
  { id: "anatomy", en: "In .git: objects holds every version of every file and every commit; refs holds the branches and the tags; HEAD says where you are.",
    it: "In punto git: objects contiene ogni versione di ogni file e ogni commit; refs contiene i rami e le etichette; HEAD dice dove sei." },
  { id: "commit", en: "A commit is an object, and git cat-file prints any object. Its type: commit. Its content: a tree, the parent commit, author, committer, and the message. Nothing else.",
    it: "Un commit è un oggetto, e git cat-file stampa qualsiasi oggetto. Il tipo: commit. Il contenuto: un tree, il commit genitore, autore, committer, e il messaggio. Nient'altro." },
  { id: "tree", en: "The tree is the directory as it was at that moment: for every file, its mode, its type, the hash of its content, and its name.",
    it: "Il tree è la directory com'era in quel momento: per ogni file, i permessi, il tipo, l'hash del contenuto, e il nome." },
  { id: "blob", en: "The blob is the content of the file, and only that: no name, no date. Its hash is computed from the content: hash-object gives the same hash for the same bytes, on any machine.",
    it: "Il blob è il contenuto del file, e solo quello: niente nome, niente data. Il suo hash si calcola dal contenuto: hash-object dà lo stesso hash per gli stessi byte, su qualsiasi macchina." },
  { id: "parent", en: "The parent line chains the commits: HEAD tilde one is the commit before. The whole history is this chain, back to the first commit, which has no parent.",
    it: "La riga parent concatena i commit: HEAD tilde uno è il commit precedente. Tutta la storia è questa catena, fino al primo commit, che non ha genitore." },
  { id: "refs", en: "And a branch? A file with one hash in it, nothing more. HEAD names the branch you are on; rev-parse turns any name into the hash it points at.",
    it: "E un ramo? Un file con dentro un hash, niente di più. HEAD nomina il ramo su cui sei; rev-parse trasforma qualsiasi nome nell'hash a cui punta." },
  { id: "amend", en: "Nothing is ever changed in place. Amend the last commit's message: the log shows a new hash. Git made a new commit and moved the branch onto it.",
    it: "Niente viene mai modificato sul posto. Correggiamo il messaggio dell'ultimo commit: il log mostra un hash nuovo. Git ha fatto un commit nuovo e ci ha spostato il ramo." },
  { id: "reflog", en: "The old one is still there: the reflog lists every position HEAD has had, the commit before the amend included. That is the safety net we will use in episode five.",
    it: "Il vecchio è ancora lì: il reflog elenca ogni posizione che HEAD ha avuto, compreso il commit di prima della correzione. È la rete di sicurezza che useremo nella quinta puntata." },
  { id: "end", top: true, en: "Next episode: branches, merges, and the first conflict.",
    it: "Nella prossima puntata: rami, merge, e il primo conflitto." },
];

export async function setup(d) {
  await courseSetup(d, AFTER_EPISODE_1);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(2000);
  await enter(d);
  await say(d, "git log --oneline", 4000);

  await d.cue("anatomy");
  await d.clearScreen();
  await say(d, "ls .git", 7000);

  await d.cue("commit");
  await d.clearScreen();
  await say(d, "git cat-file -t HEAD", 3000);
  await say(d, "git cat-file -p HEAD", 9000);

  await d.cue("tree");
  await say(d, "git cat-file -p HEAD^{tree}", 8000);

  await d.cue("blob");
  await d.clearScreen();
  await say(d, "git cat-file -p HEAD:list.txt", 5000);
  await say(d, "git rev-parse HEAD:list.txt", 3000);
  await say(d, "git hash-object list.txt", 7000);

  await d.cue("parent");
  await d.clearScreen();
  await say(d, "git cat-file -p HEAD~1", 8000);
  await say(d, "git log --oneline", 5000);

  await d.cue("refs");
  await d.clearScreen();
  await say(d, "ls .git/refs/heads", 3000);
  await say(d, "cat .git/refs/heads/main", 4000);
  await say(d, "cat .git/HEAD", 4000);
  await say(d, "git rev-parse main", 5000);

  await d.cue("amend");
  await d.clearScreen();
  await say(d, "git log --oneline -1", 3000);
  await say(d, "git commit --amend -m 'Ignore temporary files (*.tmp)'", 3500);
  await say(d, "git log --oneline -1", 6000);

  await d.cue("reflog");
  await say(d, "git reflog", 9000);
  await d.leave();

  await d.cue("end");
  await d.sleep(5000);
}
