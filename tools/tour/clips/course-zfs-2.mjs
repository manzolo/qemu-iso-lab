// ZFS course, episode 2 of 5: datasets and properties (tools/tour/zfs-course.mjs).
import { courseSetup, enter, say } from "../zfs-course.mjs";

export const title = { en: "ZFS 2/5 · Datasets and properties", it: "ZFS 2/5 · Dataset e proprietà" };
export const series = "courses";
export const lab = "zfs-lab";
export const order = 2;

export const cues = [
  { id: "intro", en: "Episode two: datasets. The pool from episode one is ready: a RAIDZ on three disks and a spare.",
    it: "Seconda puntata: i dataset. Il pool della prima puntata è pronto: un RAIDZ su tre dischi e uno spare." },
  { id: "create", en: "A dataset is a file system cut from the pool. No size to decide, no mkfs, no fstab: it is mounted as soon as it exists, and nested datasets mount inside their parent.",
    it: "Un dataset è un file system ritagliato dal pool. Nessuna dimensione da decidere, niente mkfs, niente fstab: è montato appena esiste, e i dataset annidati si montano dentro il padre." },
  { id: "compression", en: "Properties are set on a dataset and inherited by its children. Turn on lz4 compression on projects: web and db inherit it. The SOURCE column says where each value comes from.",
    it: "Le proprietà si impostano su un dataset e le ereditano i figli. Accendiamo la compressione lz4 su projects: web e db la ereditano. La colonna SOURCE dice da dove viene ogni valore." },
  { id: "ratio", en: "Copy the system's documentation into web: text compresses well, and the compression ratio says it takes a third less space on the disks. lz4 is so cheap it is worth having everywhere.",
    it: "Copiamo in web la documentazione del sistema: il testo si comprime bene, e il rapporto di compressione dice che occupa un terzo di spazio in meno sui dischi. lz4 costa così poco che conviene averlo ovunque." },
  { id: "quota", en: "A quota is a ceiling: web may not grow past two hundred megabytes. Write two hundred and fifty: the write stops at the quota with Disk quota exceeded.",
    it: "Una quota è un tetto: web non può crescere oltre duecento megabyte. Scriviamo duecentocinquanta: la scrittura si ferma alla quota con Disk quota exceeded." },
  { id: "free", en: "Delete the file. ZFS frees the space in the background, a few seconds later: right after the delete, the dataset still looks full.",
    it: "Cancelliamo il file. ZFS libera lo spazio in background, qualche secondo dopo: subito dopo la cancellazione, il dataset sembra ancora pieno." },
  { id: "reservation", en: "A reservation is the opposite: space guaranteed to a dataset. Reserve five hundred megabytes for db: it holds nothing, yet the others can use five hundred megabytes less.",
    it: "Una reservation è l'opposto: spazio garantito a un dataset. Riserviamo cinquecento megabyte a db: non contiene niente, eppure gli altri hanno cinquecento megabyte in meno." },
  { id: "recordsize", en: "recordsize is the largest block of a file. A database writes small pages, so db gets sixteen kilobytes; files that are read whole keep the default, one hundred and twenty-eight.",
    it: "recordsize è il blocco più grande di un file. Un database scrive pagine piccole, quindi db ha sedici kilobyte; i file letti per intero tengono il default, centoventotto." },
  { id: "inherit", en: "Set a property locally to override the parent, and zfs inherit to give it back: db without compression, then inheriting lz4 again.",
    it: "Una proprietà impostata in locale scavalca il padre, e zfs inherit la restituisce: db senza compressione, poi di nuovo lz4 ereditato." },
  { id: "mountpoint", en: "Where a dataset is mounted is a property too: web moves to /srv/web, with its files, in one command.",
    it: "Anche dove un dataset è montato è una proprietà: web si sposta in /srv/web, con i suoi file, in un comando." },
  { id: "end", top: true, en: "Next episode: snapshots and clones.",
    it: "Nella prossima puntata: snapshot e clone." },
];

export async function setup(d) {
  await courseSetup(d, "sudo zpool create -f tank raidz $1 $2 $3 && sudo zpool add -f tank spare $4");
}

export async function run(d) {
  await d.cue("intro");
  await enter(d);
  await say(d, "sudo zpool list tank", 4500);

  await d.cue("create");
  await d.clearScreen();
  await say(d, "sudo zfs create tank/projects", 800);
  await say(d, "sudo zfs create tank/projects/web", 800);
  await say(d, "sudo zfs create tank/projects/db", 800);
  await say(d, "sudo zfs list -r tank", 8000);

  await d.cue("compression");
  await d.clearScreen();
  await say(d, "sudo zfs set compression=lz4 tank/projects", 1000);
  await say(d, "sudo zfs get -r compression tank/projects", 9000);

  await d.cue("ratio");
  await d.clearScreen();
  await say(d, "sudo cp -r /usr/share/doc /tank/projects/web/", 1000);
  await say(d, "sudo zfs get compressratio tank/projects/web", 8000);

  await d.cue("quota");
  await d.clearScreen();
  await say(d, "sudo zfs set quota=200M tank/projects/web", 1000);
  await say(d, "sudo dd if=/dev/urandom of=/tank/projects/web/big bs=1M count=250", 3000);
  await say(d, "sudo zfs list tank/projects/web", 6000);
  await d.cue("free");
  await say(d, "sudo rm /tank/projects/web/big", 500);
  await say(d, "sudo zfs list tank/projects/web", 8000);
  await say(d, "sudo zfs list tank/projects/web", 6000);

  await d.cue("reservation");
  await d.clearScreen();
  await say(d, "sudo zfs set reservation=500M tank/projects/db", 800);
  await say(d, "sudo zfs list -r -o name,used,avail,reservation tank", 9000);

  await d.cue("recordsize");
  await d.clearScreen();
  await say(d, "sudo zfs set recordsize=16K tank/projects/db", 800);
  await say(d, "sudo zfs get -r recordsize tank/projects", 8000);

  await d.cue("inherit");
  await d.clearScreen();
  await say(d, "sudo zfs set compression=off tank/projects/db", 800);
  await say(d, "sudo zfs get -r compression tank/projects", 5000);
  await say(d, "sudo zfs inherit compression tank/projects/db", 800);
  await say(d, "sudo zfs get -r compression tank/projects", 6000);

  await d.cue("mountpoint");
  await d.clearScreen();
  await say(d, "sudo zfs set mountpoint=/srv/web tank/projects/web", 1000);
  await say(d, "ls /srv/web", 3000);
  await say(d, "sudo zfs list -r tank", 8000);
  await d.leave();
  await d.cue("end");
  await d.sleep(2500);
}
