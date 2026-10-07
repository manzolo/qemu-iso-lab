// ZFS course, episode 3 of 5: snapshots and clones (tools/tour/zfs-course.mjs).
import { courseSetup, enter, say } from "../zfs-course.mjs";

export const title = { en: "ZFS 3/5 · Snapshots and clones", it: "ZFS 3/5 · Snapshot e clone" };
export const series = "courses";
export const lab = "zfs-lab";
export const order = 3;

export const cues = [
  { id: "intro", en: "Episode three: snapshots. On the pool there is a dataset, web, with a small site in it.",
    it: "Terza puntata: gli snapshot. Sul pool c'è un dataset, web, con dentro un piccolo sito." },
  { id: "snapshot", en: "A snapshot is the dataset frozen at one instant, read-only. It is taken in no time, and it costs nothing: it shares every block with the dataset.",
    it: "Uno snapshot è il dataset congelato in un istante, in sola lettura. Si prende in un attimo, e non costa niente: condivide ogni blocco con il dataset." },
  { id: "diff", en: "Now change things: rewrite the home page, delete the stylesheet, add a page. zfs diff lists what changed since the snapshot: M modified, minus removed, plus added.",
    it: "Ora cambiamo qualcosa: riscriviamo la home page, cancelliamo il foglio di stile, aggiungiamo una pagina. zfs diff elenca cosa è cambiato dallo snapshot: M modificato, meno rimosso, più aggiunto." },
  { id: "browse", en: "Every snapshot can be browsed, in the hidden .zfs directory. Get one file back by copying it, without touching anything else.",
    it: "Ogni snapshot si può sfogliare, nella directory nascosta .zfs. Recuperi un file copiandolo, senza toccare nient'altro." },
  { id: "space", en: "A snapshot keeps what it saw. Write a hundred megabytes, take a snapshot, delete the file: the space does not come back, because the snapshot still holds those blocks.",
    it: "Uno snapshot tiene quello che ha visto. Scriviamo cento megabyte, prendiamo uno snapshot, cancelliamo il file: lo spazio non torna, perché lo snapshot tiene ancora quei blocchi." },
  { id: "rollback", en: "Rollback puts the dataset back as it was at a snapshot. Going back past a later snapshot needs -r, and destroys that later one: the hundred megabytes are free again.",
    it: "Il rollback rimette il dataset com'era a uno snapshot. Tornare indietro oltre uno snapshot più recente richiede -r, e distrugge quello più recente: i cento megabyte tornano liberi." },
  { id: "hold", en: "A hold protects a snapshot: while it has one, zfs destroy refuses. Useful for the snapshot a backup still needs. Release it, and it can go.",
    it: "Un hold protegge uno snapshot: finché ce l'ha, zfs destroy rifiuta. Utile per lo snapshot che serve ancora a un backup. Lo rilasci, e può essere distrutto." },
  { id: "clone", en: "A clone is a writable dataset born from a snapshot: a copy of the site for a test, made in an instant and taking no space until it diverges. Its origin is the snapshot.",
    it: "Un clone è un dataset scrivibile nato da uno snapshot: una copia del sito per una prova, fatta in un istante e senza occupare spazio finché non diverge. La sua origine è lo snapshot." },
  { id: "clone-end", en: "While the clone exists, its snapshot cannot be destroyed. Destroy the clone, and everything is as before.",
    it: "Finché il clone esiste, il suo snapshot non si può distruggere. Distruggi il clone, e tutto torna come prima." },
  { id: "end", top: true, en: "Next episode: faults. A disk offline, silent corruption, a scrub, and the spare taking a disk's place.",
    it: "Nella prossima puntata: i guasti. Un disco offline, una corruzione silenziosa, uno scrub, e lo spare che prende il posto di un disco." },
];

export async function setup(d) {
  await courseSetup(d, `sudo zpool create -f tank raidz $1 $2 $3 && sudo zpool add -f tank spare $4
sudo zfs create -o compression=lz4 tank/web
echo 'home, version 1' | sudo tee /tank/web/index.html >/dev/null
echo 'body { color: navy }' | sudo tee /tank/web/style.css >/dev/null
echo 'about us' | sudo tee /tank/web/about.html >/dev/null`);
}

const SNAPS = "sudo zfs list -t snapshot -r tank/web";

export async function run(d) {
  await d.cue("intro");
  await enter(d);
  await say(d, "ls /tank/web", 3000);
  await say(d, "cat /tank/web/index.html", 3000);

  await d.cue("snapshot");
  await d.clearScreen();
  await say(d, "sudo zfs snapshot tank/web@monday", 800);
  await say(d, SNAPS, 7000);

  await d.cue("diff");
  await d.clearScreen();
  await say(d, "echo 'home, version 2' | sudo tee /tank/web/index.html", 800);
  await say(d, "sudo rm /tank/web/style.css", 800);
  await say(d, "sudo touch /tank/web/contact.html", 800);
  await say(d, "sudo zfs diff tank/web@monday", 9000);

  await d.cue("browse");
  await d.clearScreen();
  await say(d, "ls /tank/web/.zfs/snapshot/monday/", 3500);
  await say(d, "cat /tank/web/.zfs/snapshot/monday/index.html", 3500);
  await say(d, "sudo cp /tank/web/.zfs/snapshot/monday/style.css /tank/web/", 1000);
  await say(d, "ls /tank/web", 5000);

  await d.cue("space");
  await d.clearScreen();
  await say(d, "sudo dd if=/dev/urandom of=/tank/web/video.bin bs=1M count=100", 2000);
  await say(d, "sudo zfs snapshot tank/web@tuesday", 800);
  await say(d, "sudo rm /tank/web/video.bin", 6000);
  await say(d, SNAPS, 9000);

  await d.cue("rollback");
  await d.clearScreen();
  await say(d, "sudo zfs rollback tank/web@monday", 4000);
  await say(d, "sudo zfs rollback -r tank/web@monday", 800);
  await say(d, "cat /tank/web/index.html", 3000);
  await say(d, SNAPS, 7000);

  await d.cue("hold");
  await d.clearScreen();
  await say(d, "sudo zfs hold keep tank/web@monday", 800);
  await say(d, "sudo zfs destroy tank/web@monday", 4000);
  await say(d, "sudo zfs holds tank/web@monday", 4000);
  await say(d, "sudo zfs release keep tank/web@monday", 2000);

  await d.cue("clone");
  await d.clearScreen();
  await say(d, "sudo zfs clone tank/web@monday tank/web-test", 800);
  await say(d, "ls /tank/web-test", 3000);
  await say(d, "sudo zfs list -r -o name,used,origin tank", 8000);
  await d.cue("clone-end");
  await d.clearScreen();
  await say(d, "sudo zfs destroy tank/web@monday", 4500);
  await say(d, "sudo zfs destroy tank/web-test", 800);
  await say(d, "sudo zfs list -t all -r tank", 6000);
  await d.leave();
  await d.cue("end");
  await d.sleep(2500);
}
