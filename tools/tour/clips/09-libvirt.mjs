// Clip 9 (2026-10-19, Manzolo: "capire come si fa l'export e import da libvirt"): a machine of the
// lab handed to libvirt as an independent copy (export-libvirt --copy-to-pool), booted there with
// its console in virt-manager, removed from vmctl, and brought back with unexport-libvirt, SSH keys
// included (0.28.1). Every step is one short command in the terminal.
// Needs in the studio: libvirt-daemon-system + virt-manager, the pool "default", the SSH user in the
// libvirt group (every d.vm and the terminal it opens is a new SSH login, so the group applies), and
// alpine-cloud installed in the demo checkout.
// A BIOS guest: recorded while the EFI round trip on libvirt 8 was being fixed (v0.28.3, the boot
// options came back pointing at libvirt's devices); its console shows the boot in virt-manager.
const VMNAME = "alpine-cloud";
const DEMO = "~/lab/demo/qemu-iso-lab";
const VIRSH = "virsh -c qemu:///system";

export const title = { en: "9 · To libvirt and back", it: "9 · In libvirt e ritorno" };

export const cues = [
  { id: "intro", en: "A machine of the lab can also go to libvirt, to keep using it in virt-manager. This small Alpine server is installed and stopped.",
    it: "Una macchina del lab può anche passare a libvirt, per usarla in virt-manager. Questo piccolo server Alpine è installato e spento." },
  { id: "export", en: "export-libvirt with copy-to-pool copies its disk into a libvirt pool: an independent machine, vmctl's own disk stays as it was.",
    it: "export-libvirt con copy-to-pool copia il suo disco in un pool di libvirt: una macchina indipendente, il disco di vmctl resta com'era." },
  { id: "list", en: "libvirt knows it now.",
    it: "Ora libvirt la conosce." },
  { id: "start", en: "It starts like any libvirt machine, and virt-manager shows its console.",
    it: "Si avvia come ogni macchina di libvirt, e virt-manager ne mostra la console." },
  { id: "shutdown", en: "Shut it down from libvirt.",
    it: "La si spegne da libvirt." },
  { id: "clean", en: "Say you remove it from vmctl: clean deletes vmctl's disk, but keeps the SSH keys the copy knows.",
    it: "Supponiamo di toglierla da vmctl: clean cancella il disco di vmctl, ma tiene le chiavi SSH che la copia conosce." },
  { id: "back", en: "The way back: unexport-libvirt downloads the copy as vmctl's disk, puts the keys back, and removes it from libvirt.",
    it: "Il ritorno: unexport-libvirt scarica la copia come disco di vmctl, rimette le chiavi e la toglie da libvirt." },
  { id: "run", en: "It is vmctl's machine again: it starts…",
    it: "È di nuovo una macchina di vmctl: si avvia…" },
  { id: "shell", en: "…and answers over SSH, with the same key as before.",
    it: "…e risponde via SSH, con la stessa chiave di prima." },
  { id: "end", en: "Without copy-to-pool the export is linked instead: libvirt runs vmctl's own disk, and unexport hands it back.",
    it: "Senza copy-to-pool l'export è collegato: libvirt usa il disco stesso di vmctl, e unexport lo restituisce." },
];

export async function setup(d) {
  // A take that stopped half way may have left the copy in libvirt: it comes back first. Then the
  // machine stopped, no link, nothing starred, every other VM down (memory for libvirt's copy).
  d.vm(`cd ${DEMO} && if ${VIRSH} dominfo ${VMNAME} >/dev/null 2>&1; then ${VIRSH} destroy ${VMNAME} >/dev/null 2>&1; ` +
       `./bin/vmctl unexport-libvirt ${VMNAME} --yes --replace >/dev/null 2>&1; fi; ` +
       `./bin/vmctl link --off --segment session >/dev/null 2>&1; ./bin/vmctl catalog clear >/dev/null 2>&1; ` +
       `for v in ${VMNAME} debian-12-cloud ubuntu-24.04-cloud; do ./bin/vmctl stop $v >/dev/null 2>&1; done; pkill -x virt-manager; true`);
  if (d.vm(`cd ${DEMO} && ./bin/vmctl status 2>/dev/null | grep -c '^${VMNAME} .*verified' || true`).trim() === "0")
    throw new Error(`${VMNAME} is not installed in the demo checkout`);
  await d.openTerminal(15);
}

async function waitFor(d, probe, seconds) {
  for (let i = 0; i < seconds; i++) {
    if (d.vm(`cd ${DEMO} && (${probe}) >/dev/null 2>&1 && echo y || echo n`).trim() === "y") return;
    await d.sleep(1000);
  }
  throw new Error(`still waiting: ${probe}`);
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(4000);

  await d.cue("export");
  await d.run(`vmctl export-libvirt ${VMNAME} --copy-to-pool`);
  await d.sleep(4000);
  await d.clearScreen();

  await d.cue("list");
  await d.run(`${VIRSH} list --all`);
  await d.sleep(2500);

  await d.cue("start");
  await d.run(`${VIRSH} start ${VMNAME}`);
  await d.run(`virt-manager -c qemu:///system --show-domain-console ${VMNAME}`);
  await waitFor(d, "DISPLAY=:0 xdotool search --name 'QEMU/KVM'", 30);
  // The console window, maximised, until the guest reaches its login prompt.
  d.vm("DISPLAY=:0 sh -c 'w=$(xdotool search --name \"QEMU/KVM\" | tail -1); xdotool windowactivate --sync $w windowsize $w 1600 860 windowmove $w 0 0'");
  await d.sleep(25000);

  await d.cue("shutdown");
  d.vm("pkill -x virt-manager; true");
  await d.sleep(800);
  await d.focusTerminal();
  await d.run(`${VIRSH} shutdown ${VMNAME}`);
  await waitFor(d, `${VIRSH} domstate ${VMNAME} | grep -q 'shut off\\|spento'`, 120);
  await d.sleep(1500);
  await d.clearScreen();

  await d.cue("clean");
  await d.run(`vmctl clean ${VMNAME}`);
  await d.sleep(4000);
  await d.clearScreen();

  await d.cue("back");
  await d.run(`vmctl unexport-libvirt ${VMNAME} --yes`);
  await d.sleep(4000);
  await d.clearScreen();

  await d.cue("run");
  await d.run(`vmctl start ${VMNAME} --headless --background`);
  await waitFor(d, `timeout 8 ./bin/vmctl shell ${VMNAME} -- true`, 120);
  await d.sleep(1000);

  await d.cue("shell");
  await d.run(`vmctl shell ${VMNAME} -- hostname`);
  await d.sleep(3000);

  await d.cue("end");
  await d.sleep(4000);
}
