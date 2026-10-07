// Shared by the five episodes of the ZFS course (tools/tour/clips/course-zfs-*.mjs, 2026-10-07):
// a slow series on the zfs-lab, one topic per episode. Every episode starts from empty lab disks
// and builds what it needs off camera (`prepare`), so each one can be recorded again on its own.
export const LAB = "zfs-lab";
export const VM = "zfs-lab-server";
export const CHECKOUT = "~/lab/demo/qemu-iso-lab";
// The four lab disks, picked by size: the guest names virtio disks by PCI slot, so the lab disks
// come first (vda..vdd) and the system disk last.
// Shown on camera wherever an episode uses the disks (Manzolo, 2026-10-07: "$DISKS come si fa a
// capirlo? va fatto anche il comando per riempire la variabile"): never prepared behind the scenes.
export const DISKS_CMD = `DISKS=$(lsblk -dnpo NAME,SIZE | awk '$2=="2G" {print $1}')`;

// The lab disks empty again: no pool, no label, nothing mounted under /srv.
const RESET = `
DISKS=$(lsblk -dnpo NAME,SIZE,TYPE | awk '$2=="2G" && $3=="disk" {print $1}' | xargs)
for p in $(sudo zpool list -H -o name 2>/dev/null); do sudo zpool destroy -f $p; done
for p in $(sudo zpool import 2>/dev/null | awk '/pool:/ {print $2}'); do sudo zpool import -f $p && sudo zpool destroy -f $p; done
for d in $DISKS; do sudo zpool labelclear -f $d >/dev/null 2>&1; sudo wipefs -aq $d; done
sudo rm -rf /srv/web
sed -i '/# zfs-course/d' ~/.bashrc
set -- $DISKS
`;

// Episode setup: the lab installed and running, empty disks, then the episode's own starting
// state (a shell script run in the guest with $DISKS and $1..$4 set), and a clean terminal.
export async function courseSetup(d, prepare = "") {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl group install ${LAB} --yes >/dev/null 2>&1; true`);
  // A stack that has just started answers SSH a little later ("Connection reset by peer" while
  // sshd comes up): that was why a rehearsal of episode four started from an empty pool, 2026-10-07.
  d.vm(`cd ${CHECKOUT} && for i in $(seq 60); do ./bin/vmctl shell ${VM} -- true >/dev/null 2>&1 && break; sleep 3; done; true`);
  // The prep must end on its marker: a take must not record an episode with nothing prepared.
  d.vm(`cat > /tmp/zfs-course-prep.sh <<'PREP'\n${RESET}\nset -e\n${prepare}\necho PREP-OK\nPREP`);
  for (let attempt = 1; ; attempt++) {
    const log = d.vm(`cd ${CHECKOUT} && ./bin/vmctl shell ${VM} -- "bash -s" < /tmp/zfs-course-prep.sh 2>&1 | tee /tmp/zfs-course-prep.log; true`);
    if (log.includes("PREP-OK")) break;
    if (attempt === 2) throw new Error(`the episode's prep failed twice (studio: /tmp/zfs-course-prep.log):\n${log.slice(-800)}`);
  }
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

// Into the server. With disks: the variable is filled on camera, then `set --` names them $1..$4;
// `explain` = the slow version of episode one (the full lsblk first, a pause after each step).
export async function enter(d, { disks = false, explain = false } = {}) {
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.session(VM);
  if (!disks) return;
  const slow = explain ? 6000 : 2500;
  if (explain) await d.guest("lsblk -dpo NAME,SIZE", { read: 6000 });
  await d.guest(DISKS_CMD, { read: 1000 });
  await d.guest("echo $DISKS", { read: slow });
  await d.guest("set -- $DISKS", { read: 1000 });
  await d.guest("echo $1 $2 $3 $4", { read: slow });
}

// A slow course: every output stays on screen long enough to be read while the voice explains it.
export const say = (d, cmd, read = 7000) => d.guest(cmd, { read });
