// ZFS course, episode 1 of 5: pools and vdevs (tools/tour/zfs-course.mjs).
import { courseSetup, say, DISKS_CMD, VM } from "../zfs-course.mjs";

export const title = { en: "ZFS 1/5 · Pools and vdevs", it: "ZFS 1/5 · Pool e vdev" };
export const series = "courses";
export const lab = "zfs-lab";
export const order = 1;

export const cues = [
  { id: "intro", en: "A ZFS course in five episodes, slowly, on the ZFS lab: a server with four empty two-gigabyte disks. This first episode is about pools, and the vdevs they are made of.",
    it: "Un corso su ZFS in cinque puntate, con calma, sul lab ZFS: un server con quattro dischi vuoti da due gigabyte. Questa prima puntata parla dei pool, e dei vdev di cui sono fatti." },
  { id: "disks", en: "First, the disks. lsblk lists them all: the system disk of ten gigabytes, and four of two. We keep the two-gigabyte ones in a variable, DISKS: picked by size, never by name, because the names follow the slots on the bus.",
    it: "Prima i dischi. lsblk li elenca tutti: il disco di sistema da dieci gigabyte, e quattro da due. Teniamo quelli da due gigabyte in una variabile, DISKS: scelti per dimensione, mai per nome, perché i nomi seguono gli slot sul bus." },
  { id: "names", en: "set gives them short names, dollar one to dollar four: the first disk to the fourth. And no pool yet.",
    it: "set dà loro dei nomi brevi, da dollaro uno a dollaro quattro: dal primo disco al quarto. E ancora nessun pool." },
  { id: "mirror", en: "The simplest pool: a mirror of two disks. One command, and the pool exists and is mounted. Its status shows the tree: the pool, a vdev called mirror-0, and the two disks inside it.",
    it: "Il pool più semplice: un mirror di due dischi. Un comando, e il pool esiste ed è già montato. Lo status mostra l'albero: il pool, un vdev chiamato mirror-0, e i due dischi dentro." },
  { id: "mirror-size", en: "Two disks of two gigabytes, and less than two usable: every block is written twice. A mirror survives the loss of one disk out of two.",
    it: "Due dischi da due gigabyte, e meno di due utilizzabili: ogni blocco è scritto due volte. Un mirror sopravvive alla perdita di un disco su due." },
  { id: "raidz", en: "Now destroy it and build a RAIDZ on three disks: data striped across them with one disk's worth of parity. Any one of the three may fail.",
    it: "Ora lo distruggiamo e costruiamo un RAIDZ su tre dischi: i dati distribuiti fra i tre con l'equivalente di un disco di parità. Uno qualsiasi dei tre può guastarsi." },
  { id: "spare", en: "The fourth disk becomes a hot spare: it belongs to the pool, holds nothing, and waits for a disk to fail. We will use it in episode four.",
    it: "Il quarto disco diventa uno spare: appartiene al pool, non contiene niente, e aspetta che un disco si guasti. Lo useremo nella quarta puntata." },
  { id: "capacity", en: "Two numbers for the size. zpool list counts the raw space, parity included: five and a half gigabytes. zfs list counts what you can store: about three and a half.",
    it: "Due numeri per la dimensione. zpool list conta lo spazio grezzo, parità compresa: cinque gigabyte e mezzo. zfs list conta quello che puoi salvare: circa tre e mezzo." },
  { id: "iostat", en: "Write some data, and zpool iostat shows where it went: spread over the three disks of the vdev, a third each, while the spare stays untouched.",
    it: "Scriviamo dei dati, e zpool iostat mostra dove sono finiti: distribuiti sui tre dischi del vdev, un terzo ciascuno, mentre lo spare resta intatto." },
  { id: "history", en: "And the pool remembers every command that changed it, with the date: zpool history.",
    it: "E il pool ricorda ogni comando che l'ha modificato, con la data: zpool history." },
  { id: "end", top: true, en: "Next episode: datasets and their properties, on this same pool.",
    it: "Nella prossima puntata: i dataset e le loro proprietà, su questo stesso pool." },
];

export async function setup(d) {
  await courseSetup(d);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(2000);
  await d.cue("disks");
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.session(VM);
  await say(d, "lsblk -dpo NAME,SIZE", 6000);
  await say(d, DISKS_CMD, 1000);
  await say(d, "echo $DISKS", 5000);
  await d.cue("names");
  await say(d, "set -- $DISKS", 1000);
  await say(d, "echo $1 $2 $3 $4", 4000);
  await say(d, "sudo zpool list", 3000);

  await d.cue("mirror");
  await d.clearScreen();
  await say(d, "sudo zpool create tank mirror $1 $2", 1500);
  await say(d, "sudo zpool status tank", 8000);
  await d.cue("mirror-size");
  await say(d, "sudo zfs list tank", 7000);

  await d.cue("raidz");
  await d.clearScreen();
  await say(d, "sudo zpool destroy tank", 1000);
  await say(d, "sudo zpool create tank raidz $1 $2 $3", 1500);
  await say(d, "sudo zpool status tank", 7000);
  await d.cue("spare");
  await d.clearScreen();
  await say(d, "sudo zpool add tank spare $4", 1500);
  await say(d, "sudo zpool status tank", 8000);

  await d.cue("capacity");
  await d.clearScreen();
  await say(d, "sudo zpool list tank", 5000);
  await say(d, "sudo zfs list tank", 7000);

  await d.cue("iostat");
  await d.clearScreen();
  await say(d, "sudo dd if=/dev/urandom of=/tank/random.bin bs=1M count=300", 2000);
  await say(d, "sudo zpool iostat -v tank", 9000);

  await d.cue("history");
  await d.clearScreen();
  await say(d, "sudo zpool history tank", 7000);
  await d.leave();
  await d.cue("end");
  await d.sleep(2500);
}
