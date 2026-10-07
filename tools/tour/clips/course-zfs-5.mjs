// ZFS course, episode 5 of 5: send/receive, export/import, and the lab back to empty disks
// (tools/tour/zfs-course.mjs). Ends with the lab's own tests, like the lessons.
import { courseSetup, enter, say, LAB } from "../zfs-course.mjs";

export const title = { en: "ZFS 5/5 · Send, receive, export, import", it: "ZFS 5/5 · Send, receive, export, import" };
export const series = "courses";
export const lab = "zfs-lab";
export const order = 5;

export const cues = [
  { id: "intro", en: "Last episode: moving data. On the pool, the web dataset with its small site; the disks in DISKS, as in episode one, for the cleanup at the end.",
    it: "Ultima puntata: spostare i dati. Sul pool, il dataset web con il suo piccolo sito; i dischi in DISKS, come nella prima puntata, per la pulizia finale." },
  { id: "send", en: "zfs send turns a snapshot into a stream of bytes, and zfs receive builds a dataset out of it. Here the copy lands in the same pool; on a real network the stream crosses ssh to another machine.",
    it: "zfs send trasforma uno snapshot in un flusso di byte, e zfs receive ne ricava un dataset. Qui la copia finisce nello stesso pool; in una rete vera il flusso passa via ssh su un'altra macchina." },
  { id: "incremental", en: "The second time, send only the difference between two snapshots, with -i. The dry run with -nv estimates the size first: a few kilobytes instead of the whole dataset.",
    it: "La seconda volta si manda solo la differenza fra due snapshot, con -i. La prova con -nv stima prima la dimensione: pochi kilobyte invece dell'intero dataset." },
  { id: "backup", en: "The backup has the new page and both snapshots: that is how ZFS backups and replication work. Proxmox does exactly this every minute, in the Proxmox lab.",
    it: "Il backup ha la pagina nuova e tutti e due gli snapshot: è così che funzionano i backup e la replica con ZFS. Proxmox fa esattamente questo ogni minuto, nel lab Proxmox." },
  { id: "export", en: "A whole pool moves too. zpool export closes it and marks it free: the disks could now go into another server.",
    it: "Si sposta anche un pool intero. zpool export lo chiude e lo segna come libero: ora i dischi potrebbero andare in un altro server." },
  { id: "import", en: "zpool import with no name searches the disks and lists the pools it finds. Import it under another name: tank comes back as archive, with all its datasets.",
    it: "zpool import senza nome cerca nei dischi ed elenca i pool che trova. Lo importiamo con un altro nome: tank torna come archive, con tutti i suoi dataset." },
  { id: "cleanup", en: "The end of the course: destroy the pool and wipe the labels, and the four disks are empty again, as the lab started.",
    it: "Fine del corso: distruggiamo il pool e cancelliamo le etichette, e i quattro dischi sono di nuovo vuoti, come all'inizio del lab." },
  { id: "tests", en: "And the lab's own tests confirm it: seven scripts, all green.",
    it: "E i test del lab lo confermano: sette script, tutti verdi." },
  { id: "end", top: true, en: "Every command of the course is in the ZFS lab's guide, in English and Italian.",
    it: "Ogni comando del corso è nella guida del lab ZFS, in inglese e in italiano." },
];

export async function setup(d) {
  await courseSetup(d, `sudo zpool create -f tank raidz $1 $2 $3 && sudo zpool add -f tank spare $4
sudo zfs create -o compression=lz4 tank/web
echo 'home, version 1' | sudo tee /tank/web/index.html >/dev/null
echo 'about us' | sudo tee /tank/web/about.html >/dev/null`);
}

export async function run(d) {
  await d.cue("intro");
  await enter(d, { disks: true });
  await say(d, "ls /tank/web", 4000);

  await d.cue("send");
  await d.clearScreen();
  await say(d, "sudo zfs snapshot tank/web@s1", 800);
  await say(d, "sudo zfs send tank/web@s1 | sudo zfs receive tank/backup", 1500);
  await say(d, "sudo zfs list -r tank", 5000);
  await say(d, "cat /tank/backup/index.html", 4000);

  await d.cue("incremental");
  await d.clearScreen();
  await say(d, "echo 'home, version 2' | sudo tee /tank/web/index.html", 800);
  await say(d, "sudo zfs snapshot tank/web@s2", 800);
  await say(d, "sudo zfs send -nv -i @s1 tank/web@s2", 6000);
  await say(d, "sudo zfs send -i @s1 tank/web@s2 | sudo zfs receive tank/backup", 1500);

  await d.cue("backup");
  await say(d, "cat /tank/backup/index.html", 3500);
  await say(d, "sudo zfs list -t snapshot -r tank/backup", 7000);

  await d.cue("export");
  await d.clearScreen();
  await say(d, "sudo zpool export tank", 1500);
  await say(d, "sudo zpool list", 5000);

  await d.cue("import");
  await d.clearScreen();
  await say(d, "sudo zpool import", 8000);
  await say(d, "sudo zpool import tank archive", 1500);
  await say(d, "sudo zfs list -r archive", 8000);

  await d.cue("cleanup");
  await d.clearScreen();
  await say(d, "sudo zpool destroy archive", 1000);
  await say(d, "for d in $DISKS; do sudo wipefs -aq $d; done", 1000);
  await say(d, "lsblk -f $DISKS", 6000);
  await d.leave();

  await d.cue("tests");
  await d.clearScreen();
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group test ${LAB}`, { wait: false });
  await d.sleep(6000);
  d.ff(6);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(4000);
  await d.cue("end");
  await d.sleep(2000);
}
