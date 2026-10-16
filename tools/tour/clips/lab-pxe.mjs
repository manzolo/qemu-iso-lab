// The pxe-lab lesson (2026-10-16): a client with an empty disk installs Debian over the network,
// and every component of the boot shows up on the map. Needs the pxe-lab installed in the demo
// checkout (setup installs it off camera when it is not), the server running, the client stopped.
// On camera: the map, the packet inspector on the client's segment NIC, the client's console while
// vmctl bootstrap-netboot reinstalls it, then the server's side of the story over SSH.
export const title = { en: "Network boot: the PXE lab", it: "Avvio dalla rete: il lab PXE" };
export const series = "labs";
export const lab = "pxe-lab";

const LAB = "pxe-lab";
const SERVER = "pxe-lab-server";
const CLIENT = "pxe-lab-client";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
const FILTER = "port 67 or port 68 or port 69";

export const cues = [
  { id: "intro", en: "A machine with an empty disk and no ISO installs Debian by itself, over the network. The PXE lab: a server that is every piece of a network install, and a diskless client.",
    it: "Una macchina con il disco vuoto e senza ISO installa Debian da sola, dalla rete. Il lab PXE: un server che è ogni pezzo di un'installazione via rete, e un client senza niente sul disco." },
  { id: "map", en: "On the map, the two machines share a private segment. The server answers DHCP, TFTP and HTTP there, and nowhere else.",
    it: "Sulla mappa le due macchine condividono un segmento privato. Il server risponde lì a DHCP, TFTP e HTTP, e da nessun'altra parte." },
  { id: "lens", en: "The lens on the client's cable opens the packet inspector. The filter keeps the first steps of a network boot: DHCP and TFTP.",
    it: "La lente sul cavo del client apre l'ispettore dei pacchetti. Il filtro tiene i primi passi di un avvio dalla rete: DHCP e TFTP." },
  { id: "reinstall", en: "From the host, wipe the client and install it again over the network.",
    it: "Dall'host cancelliamo il client e lo reinstalliamo dalla rete." },
  { id: "dhcp", en: "The client's network card boots iPXE, and DHCP appears on the cable, read from the bottom: discover, broadcast by the client; offer, from the server; request; acknowledge. Pause freezes the list.",
    it: "La scheda di rete del client avvia iPXE, e sul cavo compare il DHCP, da leggere dal basso: discover, in broadcast dal client; offer, dal server; request; acknowledge. Pausa ferma la lista." },
  { id: "menu", en: "On the client's console: pxelinux came over TFTP with the lab's menu, and its default entry loads the Debian installer over HTTP.",
    it: "Sulla console del client: pxelinux è arrivato via TFTP con il menu del lab, e la voce predefinita carica l'installer Debian via HTTP." },
  { id: "installer", en: "The installer talks on the serial console, which the host's terminal shows. It fetches its answers from the server, and packages from the Debian mirror through the server's NAT. Nobody types anything.",
    it: "L'installer parla sulla console seriale, che il terminale dell'host mostra. Prende le risposte dal server e i pacchetti dal mirror Debian, passando dal NAT del server. Nessuno scrive niente." },
  { id: "server", en: "Meanwhile on the server: dnsmasq logged the four DHCP messages, discover, offer, request, acknowledge, and every file it sent over TFTP.",
    it: "Intanto sul server: dnsmasq ha registrato i quattro messaggi DHCP, discover, offer, request, acknowledge, e ogni file mandato via TFTP." },
  { id: "lease", en: "The lease it handed out, and nginx's log: the kernel, the initrd and the answers, over HTTP.",
    it: "Il lease che ha assegnato, e il log di nginx: il kernel, l'initrd e le risposte, via HTTP." },
  { id: "done", en: "The installer powers the machine off, and it boots its own disk. Debian 13, installed from nothing but the network.",
    it: "L'installer spegne la macchina, che riparte dal suo disco. Debian 13, installata partendo solo dalla rete." },
  { id: "end", top: true, en: "Seven exercises and six tests come with the lab, in English and Italian, from its card.",
    it: "Sette esercizi e sei test accompagnano il lab, in inglese e in italiano, dalla sua card." },
];

export async function setup(d) {
  // Off camera: the lab installed (the first install of the client is the one the lesson repeats),
  // the server up, the client stopped, no other tab.
  d.vm("pkill -f 'vmct[l] bootstrap-netboot'; true");  // a take stopped halfway leaves its install running
  d.vm(`cd ${CHECKOUT} && (./bin/vmctl group status ${LAB} | grep -q '${CLIENT} .*\\(installed\\|verified\\)' || ./bin/vmctl group install ${LAB} --yes >/tmp/pxe-install.log 2>&1); ./bin/vmctl stop ${CLIENT} >/dev/null 2>&1; ./bin/vmctl group up ${LAB} >/dev/null 2>&1; ./bin/vmctl stop ${CLIENT} >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.focusBrowser();
  await d.page.bringToFront();
  await d.page.goto(d.restartWeb());
  await d.page.locator("#rows tr[data-vm]").first().waitFor({ timeout: 30000 });
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1500 300 0.1");
}

export async function run(d) {
  const p = d.page;
  const card = () => p.locator(`section.lab[data-lab="${LAB}"]`);
  await d.cue("intro");
  await d.click(p.getByRole("tab", { name: /Labs/ }));
  await d.sleep(1200);
  await card().scrollIntoViewIfNeeded();
  await d.hover(card().locator(".lab-name"));
  await d.sleep(2500);

  await d.cue("map");
  await d.click(card().locator("[data-map]"));
  const map = await d.newestPage();
  d.page = map;
  await map.locator("#map-live").filter({ hasText: /^Live/ }).waitFor({ timeout: 30000 });
  await map.evaluate(() => document.querySelector(".segment-track")?.scrollIntoView({ block: "center", behavior: "smooth" }));
  await d.sleep(1500);
  await d.hover(map.locator(`.nic.lan-nic[data-vm="${SERVER}"]`).first());
  await d.sleep(1500);
  await d.hover(map.locator(`.nic.lan-nic[data-vm="${CLIENT}"]`).first());
  await d.sleep(1500);

  await d.cue("lens");
  await d.click(map.locator(`.nic.lan-nic[data-vm="${CLIENT}"] .inspect-btn`).first());
  await map.locator("#packet-inspector").waitFor({ state: "visible" });
  await d.sleep(800);
  await d.click(map.locator("#packet-search"));
  await d.type(FILTER, 70);
  await d.key("Escape");
  await d.sleep(2000);

  await d.cue("reinstall");
  await d.openTerminal();
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.run(`vmctl clean ${CLIENT}`);
  await d.sleep(1500);
  await d.run(`vmctl bootstrap-netboot ${CLIENT}`, { wait: false });
  await d.sleep(4000);

  // The inspector covers the client's buttons: DHCP and TFTP first, frozen with Pause before the
  // installer's 40 MB come over HTTP (the menu waits 15 s), then the inspector closes for the console.
  await d.focusBrowser();
  await map.bringToFront();
  // Pause at the first DHCP row: the installer's initrd comes next at 100 frames/s and fills the
  // inspector's 500 rows in seconds (rehearsals of 2026-10-16).
  await map.locator("tr.packet-row", { hasText: /:67\b|:68\b/ }).first().waitFor({ timeout: 90000 });
  await map.evaluate(() => document.getElementById("packet-pause").click());
  await d.cue("dhcp");
  await d.hover(map.locator("#packet-rows"));
  await d.sleep(4000);

  await d.click(map.locator("#packet-close"));
  const consoleBtn = map.locator(`.vm-action[data-vm="${CLIENT}"][data-action="console"][aria-disabled="false"]`);
  await consoleBtn.waitFor({ timeout: 30000 });  // enabled by the map once the client runs
  await d.click(consoleBtn);
  const consoleWin = map.locator(`.float-window[data-vm="${CLIENT}"][data-action="console"]`);
  await consoleWin.waitFor({ timeout: 20000 });
  // The VNC session takes a few seconds: the menu must be on screen when the cue starts (the
  // first take filmed an empty frame).
  await map.frameLocator(`.float-window[data-vm="${CLIENT}"][data-action="console"] iframe`).getByText("Connected", { exact: true }).first().waitFor({ state: "attached", timeout: 20000 });
  await d.sleep(1500);  // the first frame painted
  await d.cue("menu");
  await d.hover(consoleWin);
  await d.sleep(7000);

  await d.cue("installer");
  await d.focusTerminal();
  await d.sleep(6000);
  // The install itself, fast-forwarded on the serial console in the host's terminal: about six minutes.
  d.ff(16);
  for (let i = 0; i < 900 && !d.vm(`cd ${CHECKOUT} && grep -c 'Debian preseed install complete' artifacts/${CLIENT}/logs/bootstrap-serial.log 2>/dev/null || true`).trim().match(/^[1-9]/); i++) await d.sleep(2000);
  d.ffEnd();

  await d.cue("server");
  await d.focusBrowser();
  await map.bringToFront();
  await d.click(map.locator(`.vm-action[data-vm="${SERVER}"][data-action="ssh"]`));
  const win = map.locator(`.float-window[data-vm="${SERVER}"][data-action="ssh"]`);
  await win.waitFor({ timeout: 15000 });
  const term = map.frameLocator(`.float-window[data-vm="${SERVER}"][data-action="ssh"] iframe`);
  await term.locator(".xterm-rows").filter({ hasText: /\$\s*$/m }).waitFor({ timeout: 45000 });
  await d.click(win.locator("iframe"));
  const typeIn = async (cmd, read) => { d.step(cmd); await d.type(` ${cmd}`, 60); await d.key("Return"); await d.sleep(read); };
  await typeIn("sudo journalctl -u dnsmasq -g 'DHCP[A-Z]+\\(' -n 4", 5000);
  await typeIn("sudo journalctl -u dnsmasq -g 'sent /srv' -n 4", 5000);

  await d.cue("lease");
  await typeIn("cat /var/lib/misc/dnsmasq.leases", 3500);
  await typeIn("sudo tail -4 /var/log/nginx/pxe-lab.access.log", 5000);

  await d.cue("done");
  await d.focusTerminal();
  d.ff(8);
  for (let i = 0; i < 600 && d.vm("pgrep -fc 'vmct[l] bootstrap-netboot' || true").trim() !== "0"; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(3000);
  await d.clearScreen();
  await d.run(`vmctl shell ${CLIENT} -- cat /etc/os-release`);
  await d.sleep(5000);

  await d.cue("end");
  await d.sleep(3000);
  d.page = p;
}
