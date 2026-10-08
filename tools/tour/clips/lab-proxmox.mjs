// Lesson of the proxmox-lab (2026-10-07): the cluster, ZFS replication, HA, and a node that dies.
// The stack is installed before the take (three Proxmox installs and a cluster: ~40 minutes, no
// lesson in watching them); setup() puts it back as installed: CT 200 home on node 1, no HA, no job.
export const title = { en: "Proxmox VE: a cluster, and a container that survives its node", it: "Proxmox VE: un cluster, e un container che sopravvive al suo nodo" };
export const series = "labs";
export const lab = "proxmox-lab";

const LAB = "proxmox-lab";
const N1 = "proxmox-ve";
const N2 = "proxmox-ve-node2";
const CLIENT = "proxmox-lab-client";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
const GUI = "https://127.0.0.1:8007/";  // node 2's GUI: it must survive node 1
const HA = "ha-manager status";
// Off camera: until a replication job has a LastSync.
const SYNCED = (job) => `until pvesr status | awk '$1=="${job}" && $4!="-"' | grep -q .; do sleep 3; done`;
// The client's watch loop, written off camera and shown with cat (2026-10-07: a one-line while loop
// typed on camera was unreadable).
const WATCH = `cat > ~/watch.sh <<'EOF'
while true; do
  printf '%s ' $(date +%T)
  curl -s -m 2 http://10.10.10.20/ | grep -o '<title>IT Tools' || echo 'no answer'
  sleep 5
done
EOF`;

export const cues = [
  { id: "intro", en: "Three Proxmox nodes in a cluster, and a client on the same network. One of the containers will survive the death of its node.",
    it: "Tre nodi Proxmox in cluster, e un client sulla stessa rete. Uno dei container sopravviverà alla morte del suo nodo." },
  { id: "card", en: "One command installs it all: three automated Proxmox installs, the client, then the cluster. About forty minutes, so here it is already running.",
    it: "Un comando installa tutto: tre installazioni Proxmox automatiche, il client, poi il cluster. Una quarantina di minuti, quindi eccolo già acceso." },
  { id: "cluster", en: "The cluster: three nodes, quorate. Corosync runs on the lab segment, and the quorum is the majority: two nodes out of three are enough.",
    it: "Il cluster: tre nodi, con il quorum. Corosync passa sul segmento del lab, e il quorum è la maggioranza: bastano due nodi su tre." },
  { id: "containers", en: "Node one runs two LXC containers. The first, IT-Tools, has its disk on ZFS and an address on the lab network.",
    it: "Il nodo uno fa girare due container LXC. Il primo, IT-Tools, ha il disco su ZFS e un indirizzo sulla rete del lab." },
  { id: "replica", en: "Its disk lives on node one only. Two replication jobs send it to the other nodes with ZFS, then every minute only the changes.",
    it: "Il suo disco vive solo sul nodo uno. Due job di replica lo mandano agli altri nodi con ZFS, poi ogni minuto solo le modifiche." },
  { id: "ha", en: "Now high availability: the cluster takes care of the container, and restarts it elsewhere if its node disappears.",
    it: "Ora l'alta affidabilità: il cluster si prende cura del container, e lo riavvia altrove se il suo nodo sparisce." },
  { id: "gui", en: "The web interface of node two shows the same: the container under node one, managed by HA.",
    it: "L'interfaccia web del nodo due mostra lo stesso: il container sotto il nodo uno, gestito dall'alta affidabilità." },
  { id: "loop", en: "On the client, a loop asks for the page every five seconds.",
    it: "Sul client, un ciclo chiede la pagina ogni cinque secondi." },
  { id: "kill", en: "And now we pull the plug on node one: hold the power button for two seconds, and QEMU stops at once. No shutdown.",
    it: "E ora stacchiamo la corrente al nodo uno: tieni premuto il pulsante di accensione per due secondi, e QEMU si ferma subito. Nessuno shutdown." },
  { id: "fence", en: "The other two keep the quorum. After a minute, HA marks the dead node to be fenced, and waits for its lock to expire.",
    it: "Gli altri due tengono il quorum. Dopo un minuto, l'alta affidabilità segna il nodo morto da isolare, e aspetta che scada il suo lock." },
  { id: "moved", en: "Two and a half minutes later the container runs on node two, from the last replica.",
    it: "Due minuti e mezzo dopo il container gira sul nodo due, dall'ultima replica." },
  { id: "client", en: "And the client? A few minutes without an answer, then the same page, at the same address.",
    it: "E il client? Qualche minuto senza risposta, poi la stessa pagina, allo stesso indirizzo." },
  { id: "back", en: "Node one comes back and rejoins. The replication now runs the other way, and one command brings the container home.",
    it: "Il nodo uno torna e rientra nel cluster. Ora la replica va nel verso opposto, e un comando riporta a casa il container." },
  { id: "tests", en: "Five tests, one per exercise. The last one pulls the plug again, by itself.",
    it: "Cinque test, uno per esercizio. L'ultimo stacca di nuovo la corrente, da solo." },
  { id: "end", top: true, en: "Every command is in the guide, in English and Italian, from the lab's card.",
    it: "Ogni comando è nella guida, in inglese e in italiano, dalla card del lab." },
];

// The lab as installed: CT 200 on node 1, out of HA, no replication job (what the tests also leave).
const RESET = `cd ${CHECKOUT} && ./bin/vmctl group up ${LAB} >/dev/null 2>&1; ` +
  `bash -c 'source vms/labs/_common.sh; source vms/labs/proxmox-lab/tests/_pve.sh; pve_teardown' >/dev/null 2>&1; true`;

export async function setup(d) {
  d.vm(RESET);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page) await p.close();
  await d.focusBrowser();
  await d.page.bringToFront();
  await d.page.goto(d.restartWeb());
  await d.page.locator("#rows tr[data-vm]").first().waitFor({ timeout: 30000 });
  await d.openTerminal(15);  // pvesr status is wide
  await d.focusBrowser();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1500 300 0.1");
}

const guest = (d, cmd, read = 3500) => d.guest(cmd, { read });
async function slow(d, cmd, read = 3500, factor = 6) {
  d.ff(factor);
  await d.guest(cmd, { read: 0, timeout: 600000 });
  d.ffEnd();
  await d.sleep(read);
}
const shell = (d, vm, cmd) => d.vm(`cd ${CHECKOUT} && ./bin/vmctl shell ${vm} -- "${cmd}" 2>/dev/null || true`).trim();
const haWhere = (d) => shell(d, N2, "ha-manager status | sed -n 's/^service ct:200 (\\(.*\\))$/\\1/p'");

// A row of the GUI's resource tree (a container's text carries its tags after the name).
const treeNode = (gui, text) => gui.locator(".x-tree-node-text", { hasText: new RegExp(`^${text.replace(/[()]/g, "\\$&")}`) }).filter({ visible: true }).first();

// The GUI redraws its tree every few seconds: an element can detach between finding and pointing.
async function steady(d, action) {
  for (let i = 0; ; i++) {
    try { return await action(); } catch (e) { if (i === 5) throw e; await d.sleep(800); }
  }
}

// Node 2's Proxmox GUI in its own tab, logged in as root (the certificate is self-signed).
async function openGui(d) {
  const ctx = d.page.context();
  await ctx.clearCookies({ name: "PVEAuthCookie" });  // a ticket left by an earlier run skips the login
  const gui = await ctx.newPage();
  const cdp = await ctx.newCDPSession(gui);
  await cdp.send("Security.setIgnoreCertificateErrors", { ignore: true });
  await gui.goto(GUI);
  await gui.locator("input[name=username]").waitFor({ timeout: 60000 });
  await d.sleep(800);
  await d.click(gui.locator("input[name=username]"));
  await gui.keyboard.type("root", { delay: 90 });
  await d.click(gui.locator("input[name=password]"));
  await gui.keyboard.type("lab", { delay: 90 });
  await gui.keyboard.press("Enter");
  // The "No valid subscription" box, when the post-install did not silence it.
  const nag = gui.getByRole("button", { name: "OK" });
  await nag.waitFor({ timeout: 8000 }).then(() => nag.click()).catch(() => {});
  return gui;
}

export async function run(d) {
  const p = d.page;
  const card = () => p.locator(`section.lab[data-lab="${LAB}"]`);
  await d.cue("intro");
  await d.click(p.getByRole("tab", { name: /Labs/ }));
  await p.locator("#views .active, [role=tab][aria-selected=true]").first().waitFor().catch(() => {});
  await card().scrollIntoViewIfNeeded();
  await d.hover(card().locator(".lab-name"));
  await d.sleep(2500);
  await d.cue("card");
  await d.hover(card().locator(".members"));
  await d.sleep(6000);

  await d.cue("cluster");
  await d.focusTerminal();
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.session(N1);
  await guest(d, "pvecm status", 6000);
  await guest(d, "grep ring0_addr /etc/pve/corosync.conf", 4500);

  await d.cue("containers");
  await d.clearScreen();
  await guest(d, "pct list", 2500);
  await guest(d, "pct config 200 | grep rootfs", 3500);
  await guest(d, "pct config 200 | grep net1", 4000);

  await d.cue("replica");
  await d.clearScreen();
  await guest(d, "pvesr create-local-job 200-0 proxmox-ve-node2 --schedule '*/1'", 800);
  await guest(d, "pvesr create-local-job 200-1 proxmox-ve-node3 --schedule '*/1'", 800);
  await guest(d, "pvesr schedule-now 200-0", 500);
  await guest(d, "pvesr schedule-now 200-1", 500);
  d.ff(6);
  d.offCamera(N1, SYNCED("200-0") + "; " + SYNCED("200-1"));
  d.ffEnd();
  await guest(d, "pvesr status", 5000);

  await d.cue("ha");
  await d.clearScreen();
  await guest(d, "ha-manager add ct:200 --state started --max_relocate 1 --max_restart 1", 800);
  d.ff(6);
  for (let i = 0; i < 40 && haWhere(d) !== `${N1}, started`; i++) await d.sleep(2000);
  d.ffEnd();
  await guest(d, HA, 5000);
  await d.leave();

  await d.cue("gui");
  await d.focusBrowser();
  const gui = await openGui(d);
  await treeNode(gui, N1).waitFor({ timeout: 30000 });
  await d.sleep(3000);
  // The tree opens with the nodes folded: unfold the three, so the containers show under their node.
  for (const n of [N1, N2, "proxmox-ve-node3"]) {
    await steady(d, () => d.click(treeNode(gui, n).locator("xpath=ancestor::tr[1]//*[contains(@class,'x-tree-expander')]").first(), 0.4));
    await d.sleep(400);
  }
  await steady(d, () => d.hover(treeNode(gui, "200 (it-tools)")));
  await d.sleep(1500);
  await steady(d, () => d.click(gui.getByText("HA", { exact: true }).first()));
  await d.sleep(4000);

  await d.cue("loop");
  await d.focusTerminal();
  d.offCamera(CLIENT, WATCH);
  await d.clearScreen();
  await d.session(CLIENT);
  await guest(d, "cat watch.sh", 5000);
  d.step("bash watch.sh");
  await d.type(" bash watch.sh", 40);
  await d.key("Return");
  await d.sleep(7000);

  await d.cue("kill");
  await d.focusBrowser();
  await p.bringToFront();
  await card().scrollIntoViewIfNeeded();
  const power = card().locator(`.members [data-vm="${N1}"] [data-power]`);
  const b = await d.box(power);
  d.vm(`DISPLAY=:0 ~/lab/video/mv.py ${b.x} ${b.y} 0.7 && DISPLAY=:0 xdotool mousedown 1 && sleep 2.6 && DISPLAY=:0 xdotool mouseup 1`);
  for (let i = 0; i < 30 && shell(d, N1, "true; echo up") === "up"; i++) await d.sleep(1000);
  await d.sleep(2500);

  await d.cue("fence");
  await gui.bringToFront();
  d.ff(12);
  for (let i = 0; i < 120 && !haWhere(d).includes("fence"); i++) await d.sleep(2000);
  d.ffEnd();
  await d.sleep(4000);
  await d.cue("moved");
  d.ff(12);
  for (let i = 0; i < 120 && haWhere(d) !== `${N2}, started`; i++) await d.sleep(2000);
  d.ffEnd();
  await d.sleep(3000);
  await steady(d, () => d.hover(treeNode(gui, "200 (it-tools)")));
  await d.sleep(3000);

  await d.cue("client");
  await d.focusTerminal();
  await d.sleep(7000);
  await d.key("ctrl+c");
  await d.sleep(800);
  await d.guest("true", { read: 300 });  // the session's last status is 0, not the loop's 130: no red line from vmctl
  await d.leave();

  await d.cue("back");
  await d.clearScreen();
  await d.run(`vmctl group up ${LAB}`, { wait: false });
  d.ff(10);
  await d.sleep(3000);
  for (let i = 0; i < 300 && !/Nodes:\s*3/.test(shell(d, N2, "pvecm status")); i++) await d.sleep(2000);
  d.ffEnd();
  await d.sleep(1500);
  await d.clearScreen();
  await d.session(N2);
  // The job toward node 1 failed while it was off, and a failed job waits before it retries: run it now.
  await guest(d, "pvesr schedule-now 200-0", 500);
  d.ff(8);
  d.offCamera(N2, SYNCED("200-0"));
  d.ffEnd();
  await guest(d, "pvesr status", 5000);
  await guest(d, "ha-manager migrate ct:200 proxmox-ve", 500);
  d.ff(6);
  for (let i = 0; i < 60 && haWhere(d) !== `${N1}, started`; i++) await d.sleep(2000);
  d.ffEnd();
  await guest(d, HA, 4500);
  await d.leave();

  await d.cue("tests");
  await d.clearScreen();
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group test ${LAB}`, { wait: false });
  await d.sleep(6000);
  d.ff(20);
  for (let i = 0; i < 2400 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(4000);
  await d.cue("end");
  await d.sleep(2000);
}
