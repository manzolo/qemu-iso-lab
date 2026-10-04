export const title = { en: "Software RAID with mdadm: the mdadm lab", it: "RAID software con mdadm: il lab mdadm" };
export const series = "labs";
export const lab = "mdadm-lab";

const LAB = "mdadm-lab";
const VM = "mdadm-lab-server";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
const DISKS = `DISKS=$(lsblk -dnpo NAME,SIZE,TYPE | awk '$2=="2G" && $3=="disk" {print $1}' | xargs); echo $DISKS`;
const D = (n) => `$(echo $DISKS | cut -d' ' -f${n})`;

export const cues = [
  { id: "intro", en: "RAID keeps your data when a disk dies. mdadm is the Linux kernel's own software RAID, no controller needed. The mdadm lab is one server with four empty disks: we build arrays, break them, and watch them heal.",
    it: "Il RAID tiene i tuoi dati quando un disco muore. mdadm è il RAID software del kernel Linux, senza controller. Il lab mdadm è un server con quattro dischi vuoti: costruiamo array, li rompiamo e li guardiamo guarire." },
  { id: "install", en: "One cloud image plus four 2-gigabyte disks.",
    it: "Una cloud image più quattro dischi da 2 gigabyte." },
  { id: "anatomy", en: "The four lab disks, picked by size. /proc/mdstat is the dashboard: the RAID levels the kernel can run, and no array yet. Our arrays use only 256 megabytes of each disk, so every sync takes seconds.",
    it: "I quattro dischi del lab, scelti per dimensione. /proc/mdstat è il cruscotto: i livelli RAID che il kernel sa gestire, e ancora nessun array. I nostri array usano solo 256 megabyte di ogni disco, così ogni sincronizzazione dura pochi secondi." },
  { id: "raid1", en: "A mirror: two disks, every block written to both. The first sync copies one disk onto the other; U U means both members are up. The array is a block device like any other.",
    it: "Un mirror: due dischi, ogni blocco scritto su entrambi. La prima sincronizzazione copia un disco sull'altro; U U vuol dire che i membri sono su entrambi. L'array è un dispositivo a blocchi come un altro." },
  { id: "mount", en: "Format it, mount it, write a file.",
    it: "Formattalo, montalo, scrivici un file." },
  { id: "fail", en: "Now a disk dies: mark it faulty. The array is degraded, U underscore, and the file is still readable.",
    it: "Ora un disco muore: segnalo guasto. L'array è degradato, U trattino, e il file si legge ancora." },
  { id: "replace", en: "Remove the dead member, add a disk, and the kernel rebuilds the mirror onto it: U U again.",
    it: "Togli il membro morto, aggiungi un disco, e il kernel ricostruisce il mirror sopra: di nuovo U U." },
  { id: "raid5", en: "RAID5: data and parity striped over three disks, the space of two, any one may fail. The fourth disk is declared a hot spare and waits.",
    it: "RAID5: dati e parità distribuiti su tre dischi, lo spazio di due, uno qualsiasi può guastarsi. Il quarto disco è dichiarato spare a caldo e aspetta." },
  { id: "spare", en: "Fail a member, and nobody has to be awake: the rebuild onto the spare starts by itself. Three active devices again, the file intact.",
    it: "Fai fallire un membro, e nessuno deve essere sveglio: la ricostruzione sullo spare parte da sola. Di nuovo tre dispositivi attivi, il file intatto." },
  { id: "grow", en: "Grow it online: the failed disk comes back as a new member and the array reshapes from three devices to four, mounted all along. resize2fs takes the new space.",
    it: "Allargalo a caldo: il disco guasto torna come nuovo membro e l'array si rimodella da tre dispositivi a quattro, restando montato. resize2fs prende lo spazio nuovo." },
  { id: "assemble", en: "detail scan prints the line mdadm.conf wants for the next boot. Stop the array, assemble it again from the superblocks on its members: the file is there.",
    it: "detail scan stampa la riga che mdadm.conf vuole per il prossimo boot. Ferma l'array, riassemblalo dai superblocchi sui suoi membri: il file c'è." },
  { id: "teardown", en: "Zeroing the superblocks is what ends an array. Four empty disks again.",
    it: "Azzerare i superblocchi è ciò che chiude un array. Di nuovo quattro dischi vuoti." },
  { id: "tests", en: "The lab's six tests do all of this again, waiting for every sync, and leave the disks empty.",
    it: "I sei test del lab rifanno tutto, aspettando ogni sincronizzazione, e lasciano i dischi vuoti." },
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

// mdadm syncs on 2 GiB disks take seconds: wait for /proc/mdstat to settle before reading it.
const WAIT = "while grep -qE 'resync|recovery|reshape' /proc/mdstat; do sleep 1; done";

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

  await d.cue("anatomy");
  await d.run("clear");
  await d.session(VM);
  await d.guest(DISKS, { read: 3000 });
  await d.guest("cat /proc/mdstat", { read: 4000 });

  await d.cue("raid1");
  await d.guest(`sudo mdadm --create /dev/md0 --size=256M --run --level=1 --raid-devices=2 ${D(1)} ${D(2)}`, { read: 2500 });
  await d.guest(`${WAIT}; cat /proc/mdstat`, { read: 5000, timeout: 180000 });
  await d.guest("sudo mdadm --detail /dev/md0 | grep -E 'Raid Level|Array Size|State :|Active|Working'", { read: 4500 });
  await d.cue("mount");
  await d.guest("sudo mkfs.ext4 -F -q /dev/md0 && sudo mkdir -p /mnt/raid1 && sudo mount /dev/md0 /mnt/raid1 && echo mirrored | sudo tee /mnt/raid1/file", { read: 3500 });

  await d.cue("fail");
  await d.guest(`clear; sudo mdadm /dev/md0 --fail ${D(1)} && cat /proc/mdstat`, { read: 5000 });
  await d.guest("cat /mnt/raid1/file    # still readable, degraded", { read: 3500 });
  await d.cue("replace");
  await d.guest(`sudo mdadm /dev/md0 --remove ${D(1)} && sudo mdadm /dev/md0 --add ${D(1)}`, { read: 2000 });
  await d.guest(`${WAIT}; cat /proc/mdstat`, { read: 5000, timeout: 180000 });

  await d.cue("raid5");
  await d.guest(`clear; sudo umount /mnt/raid1; sudo mdadm --stop /dev/md0; sudo mdadm --zero-superblock ${D(1)} ${D(2)}`, { read: 2000 });
  await d.guest("sudo mdadm --create /dev/md1 --size=256M --run --level=5 --raid-devices=3 --spare-devices=1 $DISKS", { read: 2500 });
  await d.guest(`${WAIT}; sudo mdadm --detail /dev/md1 | grep -E 'Raid Level|Array Size|State :|Active|Spare'`, { read: 5500, timeout: 300000 });
  await d.guest("sudo mkfs.ext4 -F -q /dev/md1 && sudo mkdir -p /mnt/raid5 && sudo mount /dev/md1 /mnt/raid5 && echo striped | sudo tee /mnt/raid5/file", { read: 3000 });

  await d.cue("spare");
  await d.guest(`clear; sudo mdadm /dev/md1 --fail ${D(2)} && sleep 2 && cat /proc/mdstat    # the spare rebuilds, unasked`, { read: 5000 });
  await d.guest(`${WAIT}; sudo mdadm --detail /dev/md1 | grep -E 'State :|Active|Working|Failed|Spare'; cat /mnt/raid5/file`, { read: 5500, timeout: 300000 });

  await d.cue("grow");
  await d.guest(`clear; sudo mdadm /dev/md1 --remove ${D(2)} && sudo mdadm /dev/md1 --add ${D(2)} && sudo mdadm --grow /dev/md1 --raid-devices=4 && cat /proc/mdstat    # reshape`, { read: 5000 });
  await d.guest(`${WAIT}; sudo mdadm --detail /dev/md1 | grep -E 'Raid Devices|Array Size'`, { read: 4000, timeout: 600000 });
  await d.guest("sudo resize2fs /dev/md1 2>/dev/null; df -h /mnt/raid5    # from 512 to 768 MiB, still mounted", { read: 5000 });

  await d.cue("assemble");
  await d.guest("clear; sudo mdadm --detail --scan", { read: 3500 });
  await d.guest("sudo umount /mnt/raid5 && sudo mdadm --stop /dev/md1 && cat /proc/mdstat", { read: 3500 });
  await d.guest("sudo mdadm --assemble --scan && cat /proc/mdstat && sudo mount /dev/md1 /mnt/raid5 && cat /mnt/raid5/file", { read: 5000 });

  await d.cue("teardown");
  await d.guest("sudo umount /mnt/raid5; sudo mdadm --stop /dev/md1; for d in $DISKS; do sudo mdadm --zero-superblock $d; sudo wipefs -a $d; done; cat /proc/mdstat; lsblk", { read: 5000 });
  await d.leave();

  await d.cue("tests");
  await d.run("clear");
  const before2 = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group test ${LAB}`, { wait: false });
  await d.sleep(6000);
  d.ff(8);
  for (let i = 0; i < 1200 && d.vm("cat /tmp/demo-prompt") === before2; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(4000);
  await d.cue("end");
  await d.sleep(2000);
}
