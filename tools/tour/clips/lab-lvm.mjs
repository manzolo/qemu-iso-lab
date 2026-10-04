export const title = { en: "LVM from scratch: the LVM lab", it: "LVM da zero: il lab LVM" };
export const series = "labs";  // a lab lesson, not a chapter of the tour

const LAB = "lvm-lab";
const VM = "lvm-lab-server";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
const DISKS = `DISKS=$(lsblk -dnpo NAME,SIZE,TYPE | awk '$2=="2G" && $3=="disk" {print $1}' | xargs); echo $DISKS`;

export const lab = "lvm-lab";  // the catalog lab this clip explains: the site links it from the lab card

export const cues = [
  { id: "intro", en: "LVM, Logical Volume Management, is how Linux stops a disk from being a fixed box: storage becomes a pool you cut volumes from, and resize later. The LVM lab is one server with three empty disks to practise on.",
    it: "LVM, Logical Volume Management, è il modo in cui Linux smette di trattare un disco come una scatola fissa: lo spazio diventa un serbatoio da cui ritagliare volumi, da ridimensionare dopo. Il lab LVM è un server con tre dischi vuoti su cui fare pratica." },
  { id: "install", en: "Install it from the terminal: one cloud image plus three 2-gigabyte disks, ready in a couple of minutes.",
    it: "Si installa dal terminale: una cloud image più tre dischi da 2 gigabyte, pronti in un paio di minuti." },
  { id: "shell", en: "vmctl shell opens a session on the server. From here on, everything is plain LVM, the same on any Linux.",
    it: "vmctl shell apre una sessione sul server. Da qui in poi è LVM puro, uguale su qualsiasi Linux." },
  { id: "disks", en: "lsblk lists the block devices: the system disk with its partitions, and three bare 2-gigabyte disks. We pick those by size, never by name.",
    it: "lsblk elenca i dispositivi a blocchi: il disco di sistema con le sue partizioni, e tre dischi nudi da 2 gigabyte. Li scegliamo per dimensione, mai per nome." },
  { id: "layers", en: "LVM has three layers. Physical volumes: disks labelled for LVM. A volume group: the pool that adds them up. Logical volumes: the slices you format and mount.",
    it: "LVM ha tre strati. I physical volume: dischi etichettati per LVM. Il volume group: il serbatoio che li somma. I logical volume: le fette che formatti e monti." },
  { id: "pv", en: "pvcreate writes the LVM label on each disk. pvs shows them: three physical volumes, not yet in any group.",
    it: "pvcreate scrive l'etichetta LVM su ogni disco. pvs li mostra: tre physical volume, ancora in nessun gruppo." },
  { id: "vg", en: "vgcreate pools them into a group called labvg: about 6 gigabytes, the sum of the three, minus a little metadata.",
    it: "vgcreate li mette insieme in un gruppo chiamato labvg: circa 6 gigabyte, la somma dei tre, meno un po' di metadati." },
  { id: "lv", en: "lvcreate cuts a logical volume of 1 gigabyte called data, and another called logs. Each is a block device: format it like a partition.",
    it: "lvcreate ritaglia un logical volume da 1 gigabyte chiamato data, e un altro chiamato logs. Ognuno è un dispositivo a blocchi: lo si formatta come una partizione." },
  { id: "mount", en: "ext4 on one, xfs on the other, mounted under /mnt. df shows two file systems of 1 gigabyte each; the group still has 4 free.",
    it: "ext4 su uno, xfs sull'altro, montati sotto /mnt. df mostra due file system da 1 gigabyte; nel gruppo ne restano 4 liberi." },
  { id: "grow", en: "Here is the point of LVM. Write a file, then grow the volume by 1 gigabyte while it is mounted: lvextend with resizefs enlarges the volume and the file system in one step.",
    it: "Ecco il senso di LVM. Scrivi un file, poi allarga il volume di 1 gigabyte mentre è montato: lvextend con resizefs ingrandisce volume e file system in un colpo solo." },
  { id: "grown", en: "df says 2 gigabytes now, and the file is still there. No unmount, no downtime.",
    it: "df dice 2 gigabyte adesso, e il file è ancora lì. Niente smontaggio, niente fermo." },
  { id: "snap", en: "Snapshots: freeze the volume as it is, change the file, then merge the snapshot back. The change is undone.",
    it: "Gli snapshot: congeli il volume com'è, cambi il file, poi fai rientrare lo snapshot. La modifica è annullata." },
  { id: "teardown", en: "Back to three empty disks: remove the group, remove the labels, wipe. The lab is ready for the next run.",
    it: "Si torna a tre dischi vuoti: via il gruppo, via le etichette, pulizia. Il lab è pronto per il prossimo giro." },
  { id: "tests", en: "The same steps are the lab's tests: vmctl group test runs all seven and tells you if your LVM still behaves.",
    it: "Gli stessi passi sono i test del lab: vmctl group test li esegue tutti e sette e ti dice se il tuo LVM si comporta ancora bene." },
  { id: "end", top: true, en: "The guide, in English and Italian, has every command with its explanation: open it from the lab's card.",
    it: "La guida, in inglese e in italiano, ha ogni comando con la sua spiegazione: si apre dalla card del lab." },
];

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl stop ${VM} >/dev/null 2>&1; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

const guest = (d, cmd, read = 3500) => d.guest(cmd, { read });

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab");
  await d.sleep(2500);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(5000);
  d.ff(12);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(1500);

  await d.cue("shell");
  await d.run("clear");
  await d.session(VM);
  await d.cue("disks");
  await guest(d, "lsblk", 5000);
  await guest(d, DISKS, 3500);
  await d.cue("layers");
  await guest(d, "sudo pvs; sudo vgs; sudo lvs    # nothing yet", 4000);

  await d.cue("pv");
  await guest(d, "sudo pvcreate $DISKS", 2500);
  await guest(d, "sudo pvs", 3500);
  await d.cue("vg");
  await guest(d, "sudo vgcreate labvg $DISKS", 2500);
  await guest(d, "sudo vgs labvg", 3500);

  await d.cue("lv");
  await guest(d, "sudo lvcreate -L 1G -n data labvg", 2500);
  await guest(d, "sudo lvcreate -L 1G -n logs labvg", 2500);
  await guest(d, "sudo lvs labvg", 3000);
  await d.cue("mount");
  await guest(d, "sudo mkfs.ext4 -F -q /dev/labvg/data && sudo mkfs.xfs -f -q /dev/labvg/logs", 4000);
  await guest(d, "sudo mkdir -p /mnt/lab-data /mnt/lab-logs && sudo mount /dev/labvg/data /mnt/lab-data && sudo mount /dev/labvg/logs /mnt/lab-logs", 2500);
  await guest(d, "df -h /mnt/lab-data /mnt/lab-logs; sudo vgs labvg", 5000);

  await d.cue("grow");
  await guest(d, "echo before-growth | sudo tee /mnt/lab-data/keep", 2000);
  await guest(d, "sudo lvextend -L +1G --resizefs labvg/data", 5000);
  await d.cue("grown");
  await guest(d, "df -h /mnt/lab-data; cat /mnt/lab-data/keep", 5000);

  await d.cue("snap");
  await guest(d, "echo version-1 | sudo tee /mnt/lab-data/file", 1800);
  await guest(d, "sudo lvcreate -s -L 256M -n data-snap labvg/data", 2500);
  await guest(d, "echo version-2 | sudo tee /mnt/lab-data/file    # the change to undo", 1800);
  await guest(d, "sudo umount /mnt/lab-data; sudo lvconvert --merge labvg/data-snap", 3000);
  await guest(d, "sudo lvchange -an labvg/data; sudo lvchange -ay labvg/data; sudo lvs labvg    # the snapshot is gone: merged", 4000);
  await guest(d, "sudo mount /dev/labvg/data /mnt/lab-data; cat /mnt/lab-data/file    # version-1 again", 4500);

  await d.cue("teardown");
  await guest(d, "sudo umount /mnt/lab-data /mnt/lab-logs; sudo vgremove -fy labvg", 3500);
  await guest(d, "sudo pvremove -y $DISKS; sudo wipefs -a $DISKS", 3000);
  await guest(d, "lsblk", 4000);
  await d.leave();

  await d.cue("tests");
  await d.sleep(800);
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
