// ZFS course, episode 4 of 5: faults and maintenance (tools/tour/zfs-course.mjs).
import { courseSetup, enter, say } from "../zfs-course.mjs";

export const title = { en: "ZFS 4/5 · Faults, scrub and spare", it: "ZFS 4/5 · Guasti, scrub e spare" };
export const series = "courses";
export const lab = "zfs-lab";
export const order = 4;

export const cues = [
  { id: "intro", en: "Episode four: things break. As in episode one, the four disks go into DISKS, and into dollar one to dollar four.",
    it: "Quarta puntata: le cose si rompono. Come nella prima puntata, i quattro dischi vanno in DISKS, e in dollaro uno, dollaro quattro." },
  { id: "data", en: "On the pool, three hundred megabytes of data, and their checksum, so we can tell whether a single byte changes.",
    it: "Sul pool, trecento megabyte di dati, e il loro checksum, per accorgerci se cambia anche un solo byte." },
  { id: "offline", en: "A disk goes offline. The pool is DEGRADED, and it keeps working: the data is still readable, and a write still lands.",
    it: "Un disco va offline. Il pool è DEGRADED, e continua a funzionare: i dati si leggono ancora, e una scrittura arriva lo stesso." },
  { id: "online", en: "The disk comes back. ZFS resilvers it: it copies only the blocks the disk missed while it was away, not the whole disk.",
    it: "Il disco torna. ZFS lo risincronizza con un resilver: copia solo i blocchi che il disco ha perso mentre era via, non tutto il disco." },
  { id: "corrupt", en: "Now the worst kind of fault: silent corruption. We write random bytes in the middle of one disk, behind ZFS's back. Never do this to a real disk.",
    it: "Ora il guasto peggiore: la corruzione silenziosa. Scriviamo byte casuali in mezzo a un disco, alle spalle di ZFS. Non fatelo mai su un disco vero." },
  { id: "scrub", en: "A scrub reads every block of the pool and compares it with its checksum. It finds the damaged blocks, rebuilds them from parity, and counts them in the CKSUM column. No data lost.",
    it: "Uno scrub legge ogni blocco del pool e lo confronta con il suo checksum. Trova i blocchi danneggiati, li ricostruisce dalla parità, e li conta nella colonna CKSUM. Nessun dato perso." },
  { id: "verify", en: "The file's checksum is the same as before. zpool clear resets the counters. Ubuntu already runs a scrub every month, from cron.",
    it: "Il checksum del file è lo stesso di prima. zpool clear azzera i contatori. Ubuntu fa già uno scrub ogni mese, da cron." },
  { id: "replace", en: "A disk that keeps returning errors gets replaced. Here the spare takes its place: zpool replace, and the resilver writes onto the spare what that disk held.",
    it: "Un disco che continua a dare errori va sostituito. Qui lo spare prende il suo posto: zpool replace, e il resilver scrive sullo spare quello che c'era su quel disco." },
  { id: "detach", en: "Detach the old disk, and the spare is a full member of the RAIDZ. Three disks again, no spare left: the pool is healthy, and its data intact.",
    it: "Stacchiamo il vecchio disco, e lo spare diventa un membro a tutti gli effetti del RAIDZ. Di nuovo tre dischi, nessuno spare: il pool è sano, e i dati intatti." },
  { id: "end", top: true, en: "Last episode: moving data with send and receive, and moving a whole pool with export and import.",
    it: "Ultima puntata: spostare i dati con send e receive, e spostare un pool intero con export e import." },
];

export async function setup(d) {
  await courseSetup(d, `sudo zpool create -f tank raidz $1 $2 $3 && sudo zpool add -f tank spare $4
sudo zfs create tank/data
sudo dd if=/dev/urandom of=/tank/data/archive.bin bs=1M count=300 status=none; sync
sha256sum /tank/data/archive.bin | sudo tee /root/archive.sha256 >/dev/null`);
}

export async function run(d) {
  await d.cue("intro");
  await enter(d, { disks: true });
  await d.cue("data");
  await d.clearScreen();
  await say(d, "ls -lh /tank/data", 3500);
  await say(d, "sha256sum /tank/data/archive.bin", 6000);

  await d.cue("offline");
  await d.clearScreen();
  await say(d, "sudo zpool offline tank $1", 800);
  await say(d, "sudo zpool status tank", 8000);
  await say(d, "echo still-writing | sudo tee /tank/data/note.txt", 3000);

  await d.cue("online");
  await d.clearScreen();
  await say(d, "sudo zpool online tank $1", 3000);
  await say(d, "sudo zpool status tank", 8000);

  await d.cue("corrupt");
  await d.clearScreen();
  await say(d, "sudo dd if=/dev/urandom of=$2 bs=1M seek=300 count=600 conv=notrunc", 4000);

  await d.cue("scrub");
  await d.clearScreen();
  await say(d, "sudo zpool scrub -w tank", 800);
  await say(d, "sudo zpool status tank", 10000);

  await d.cue("verify");
  await d.clearScreen();
  await say(d, "sha256sum /tank/data/archive.bin", 5000);
  await say(d, "sudo zpool clear tank", 1000);
  await say(d, "cat /etc/cron.d/zfsutils-linux", 6000);

  await d.cue("replace");
  await d.clearScreen();
  await say(d, "sudo zpool replace tank $2 $4", 5000);
  await say(d, "sudo zpool status tank", 9000);

  await d.cue("detach");
  await d.clearScreen();
  await say(d, "sudo zpool detach tank $2", 800);
  await say(d, "sudo zpool status tank", 7000);
  await d.leave();
  await d.cue("end");
  await d.sleep(2500);
}
