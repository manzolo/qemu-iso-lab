export const title = { en: "ZFS from the first pool: the ZFS lab", it: "ZFS dal primo pool: il lab ZFS" };
export const series = "labs";
export const lab = "zfs-lab";

const LAB = "zfs-lab";
const VM = "zfs-lab-server";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
const DISKS = `DISKS=$(lsblk -dnpo NAME,SIZE,TYPE | awk '$2=="2G" && $3=="disk" {print $1}' | xargs); echo $DISKS`;

export const cues = [
  { id: "intro", en: "ZFS is the volume manager and the file system in one: a pool of disks, file systems cut from it, a checksum on every block. The ZFS lab is one server with four empty disks to pool, break a little, and wipe.",
    it: "ZFS è volume manager e file system insieme: un pool di dischi, file system ritagliati da lì, un checksum su ogni blocco. Il lab ZFS è un server con quattro dischi vuoti da mettere in pool, rompere un po' e ripulire." },
  { id: "install", en: "One cloud image plus four 2-gigabyte disks; the install loads the zfs module and the tools.",
    it: "Una cloud image più quattro dischi da 2 gigabyte; l'installazione carica il modulo zfs e gli strumenti." },
  { id: "disks", en: "The four lab disks, picked by size, never by name. No pool yet.",
    it: "I quattro dischi del lab, scelti per dimensione, mai per nome. Ancora nessun pool." },
  { id: "pool", en: "One command makes the pool: RAIDZ over the four disks, data striped with one disk of parity, any one may fail. It is already mounted at /tank: no partitions, no mkfs, no fstab.",
    it: "Un comando crea il pool: RAIDZ sui quattro dischi, dati distribuiti con un disco di parità, uno qualsiasi può guastarsi. È già montato in /tank: niente partizioni, niente mkfs, niente fstab." },
  { id: "dataset", en: "A dataset is a file system cut from the pool, with its own properties and no size to decide. Turn on lz4 compression and write fifty megabytes of repetitive text…",
    it: "Un dataset è un file system ritagliato dal pool, con le sue proprietà e nessuna dimensione da decidere. Accendi la compressione lz4 e scrivi cinquanta megabyte di testo ripetitivo…" },
  { id: "ratio", en: "…and the compression ratio says how much of it was ever written to disk. That is free.",
    it: "…e il rapporto di compressione dice quanto ne è finito davvero su disco. È gratis." },
  { id: "snapshot", en: "Snapshots: freeze the dataset, change a file, and zfs diff shows what moved since. Rollback puts it back as it was.",
    it: "Gli snapshot: congeli il dataset, cambi un file, e zfs diff mostra cosa si è mosso da allora. Il rollback lo rimette com'era." },
  { id: "offline", en: "Now break it. One disk goes offline: the pool is DEGRADED and keeps serving; a write still lands.",
    it: "Ora rompiamolo. Un disco va offline: il pool è DEGRADED e continua a servire; una scrittura arriva lo stesso." },
  { id: "resilver", en: "The disk comes back and ZFS resilvers only the blocks it missed. Then a scrub reads every block against its checksum: repaired nothing, no known data errors.",
    it: "Il disco torna e ZFS risincronizza solo i blocchi che gli mancavano. Poi uno scrub legge ogni blocco confrontandolo col checksum: niente da riparare, nessun errore." },
  { id: "send", en: "send turns a snapshot into a stream, receive makes a dataset out of it: a backup here, a replica on another machine through ssh.",
    it: "send trasforma uno snapshot in un flusso, receive ne ricava un dataset: un backup qui, una replica su un'altra macchina via ssh." },
  { id: "mirror", en: "Last, the other layout: two mirrored pairs. Less space than RAIDZ over the same disks, but a resilver copies one whole disk at full speed. Then the disks go back empty.",
    it: "Infine l'altra disposizione: due coppie in mirror. Meno spazio del RAIDZ sugli stessi dischi, ma un resilver copia un disco intero a piena velocità. Poi i dischi tornano vuoti." },
  { id: "tests", en: "The lab's seven tests do all of this again and leave the disks empty for the next run.",
    it: "I sette test del lab rifanno tutto questo e lasciano i dischi vuoti per il prossimo giro." },
  { id: "end", top: true, en: "Every command is in the guide, in English and Italian, from the lab's card.",
    it: "Ogni comando è nella guida, in inglese e in italiano, dalla card del lab." },
];

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl stop ${VM} >/dev/null 2>&1; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab");
  await d.sleep(3500);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(5000);
  d.ff(12);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(1500);

  await d.cue("disks");
  await d.run("clear");
  await d.session(VM);
  await d.guest(DISKS, { read: 3500 });
  await d.guest("sudo zpool list", { read: 2500 });

  await d.cue("pool");
  await d.guest("sudo zpool create -f tank raidz $DISKS && sudo zpool status tank", { read: 6000 });
  await d.guest("sudo zpool list -o name,size,alloc,free,health tank; df -h /tank", { read: 4500 });

  await d.cue("dataset");
  await d.guest("clear; sudo zfs create tank/data && sudo zfs set compression=lz4 tank/data", { read: 2000 });
  await d.guest("yes 'the same line, again and again, compresses very well' | head -c 50M | sudo tee /tank/data/text.bin >/dev/null; sync", { read: 2500 });
  await d.cue("ratio");
  await d.guest("sudo zfs get -H -o value compressratio tank/data", { read: 3500 });
  await d.guest("sudo zfs list -o name,used,avail,refer,mountpoint", { read: 4500 });

  await d.cue("snapshot");
  await d.guest("clear; echo version-1 | sudo tee /tank/data/file && sudo zfs snapshot tank/data@v1", { read: 2000 });
  await d.guest("echo version-2 | sudo tee /tank/data/file && sudo zfs diff tank/data@v1", { read: 3500 });
  await d.guest("sudo zfs rollback tank/data@v1 && cat /tank/data/file    # version-1 again", { read: 4000 });

  await d.cue("offline");
  await d.guest("clear; sudo zpool offline tank $(echo $DISKS | cut -d' ' -f1) && sudo zpool status tank", { read: 6000 });
  await d.guest("echo written-while-degraded | sudo tee /tank/data/degraded", { read: 2500 });
  await d.cue("resilver");
  await d.guest("sudo zpool online tank $(echo $DISKS | cut -d' ' -f1) && sleep 3 && sudo zpool status tank", { read: 5500 });
  await d.guest("clear; sudo zpool scrub tank && sleep 4 && sudo zpool status tank", { read: 6000 });

  await d.cue("send");
  await d.guest("clear; sudo zfs snapshot tank/data@backup && sudo zfs send tank/data@backup | sudo zfs receive tank/copy", { read: 3000 });
  await d.guest("sudo zfs list; cat /tank/copy/file", { read: 4500 });
  await d.guest("sudo zfs destroy -r tank/copy", { read: 1500 });

  await d.cue("mirror");
  await d.guest("clear; sudo zpool destroy tank && sudo zpool create -f tank mirror $(echo $DISKS | cut -d' ' -f1,2) mirror $(echo $DISKS | cut -d' ' -f3,4)", { read: 2500 });
  await d.guest("sudo zpool status tank; sudo zfs list tank", { read: 6000 });
  await d.guest("sudo zpool destroy tank; for d in $DISKS; do sudo zpool labelclear -f $d; sudo wipefs -a $d; done; lsblk", { read: 4500 });
  await d.leave();

  await d.cue("tests");
  await d.run("clear");
  const before2 = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group test ${LAB}`, { wait: false });
  await d.sleep(6000);
  d.ff(6);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before2; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(4000);
  await d.cue("end");
  await d.sleep(2000);
}
