// The showcase (v0.26.0, 2026-10-06): one ~3-minute film for the README, the whole project at a
// glance on the studio's demo checkout. Ubuntu 26.04 desktop installed unattended from the web,
// its live preview and console, a command over SSH, a second machine (ubuntu-24.04-cloud,
// installed in setup) linked to it on /multi, the session map with its lights, the power button.
// Needs: isos/ubuntu-26.04-live-server-amd64.iso and the 24.04 cloud image in the demo checkout.
const DESKTOP = "ubuntu-26.04";
const SERVER = "ubuntu-24.04-cloud";
const DEMO = "~/lab/demo/qemu-iso-lab";

export const title = { en: "QEMU ISO Lab in three minutes", it: "QEMU ISO Lab in tre minuti" };
export const order = 0;

export const cues = [
  { id: "hello", en: "QEMU ISO Lab: virtual machines and whole labs on plain QEMU, from one dashboard in your browser.",
    it: "QEMU ISO Lab: macchine virtuali e laboratori interi su QEMU, da una dashboard nel browser." },
  { id: "pick", en: "The catalog has almost two hundred ready profiles. Let us take Ubuntu 26.04 Desktop.",
    it: "Il catalogo ha quasi duecento profili pronti. Prendiamo Ubuntu 26.04 Desktop." },
  { id: "install", en: "Install automatically: no installer to click through, no questions.",
    it: "Install automatically: nessun installer da cliccare, nessuna domanda." },
  { id: "log", en: "Every action is a job with its live log. Here the installation, sped up.",
    it: "Ogni azione è un job con il suo log dal vivo. Qui l'installazione, accelerata." },
  { id: "done", en: "Installed, verified, and running. The row climbs to the top and lights up.",
    it: "Installata, verificata e accesa. La riga sale in cima e si illumina." },
  { id: "preview", en: "Rest the pointer on its icon: a live preview of the screen, wherever the desktop is now.",
    it: "Basta fermare il puntatore sull'icona: un'anteprima dal vivo dello schermo, ovunque sia il desktop in quel momento." },
  { id: "console", en: "Open console: the desktop in the browser, with keyboard, clipboard, files and screenshots.",
    it: "Open console: il desktop nel browser, con tastiera, appunti, file e screenshot." },
  { id: "shell", en: "Or from a terminal: vmctl shell runs commands in the guest over SSH, with the project's own key.",
    it: "Oppure da un terminale: vmctl shell esegue comandi nella macchina via SSH, con la chiave del progetto." },
  { id: "two", en: "A second machine: a small Ubuntu server. Select both, and start them in console.",
    it: "Una seconda macchina: un piccolo server Ubuntu. Si selezionano tutte e due e si avviano in console." },
  { id: "multi", en: "Their consoles side by side. Link network puts them on a private network of their own.",
    it: "Le loro console affiancate. Link network le mette su una rete privata tutta loro." },
  { id: "info", en: "The i tells who has which address.",
    it: "La i dice chi ha quale indirizzo." },
  { id: "map", en: "The map draws that network live. A ping between the two, and the cables light up.",
    it: "La mappa disegna quella rete dal vivo. Un ping tra le due, e i cavi si accendono." },
  { id: "power", en: "Every machine has its power button. Hold it for two seconds and the machine is forced off.",
    it: "Ogni macchina ha il suo pulsante di accensione. Tenuto premuto due secondi, la macchina si spegne subito." },
  { id: "again", en: "One click turns it back on, and the list follows it.",
    it: "Un clic la riaccende, e la lista la segue." },
  { id: "end", en: "Labs, checkpoints, the catalog and much more: QEMU ISO Lab, on GitHub.",
    it: "Laboratori, checkpoint, il catalogo e molto altro: QEMU ISO Lab, su GitHub." },
];

const iconOf = (p, vm) => p.locator(`#rows tr[data-vm="${vm}"] .vm-select`);
const powerOf = (p, vm) => p.locator(`#rows tr[data-vm="${vm}"] [data-power]`);

export async function setup(d) {
  // The desktop is installed on camera; the server is ready and stopped, the link of an earlier
  // take is gone, nothing starred, no other tab.
  d.vm(`cd ${DEMO} && ./bin/vmctl link --off --segment session >/dev/null 2>&1; ` +
       `./bin/vmctl stop ${DESKTOP} --force >/dev/null 2>&1; ./bin/vmctl clean ${DESKTOP} --starred >/dev/null 2>&1; ` +
       `./bin/vmctl stop ${SERVER} >/dev/null 2>&1; ./bin/vmctl catalog clear >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.focusBrowser();
  await d.page.bringToFront();
  await d.page.goto(d.restartWeb());
  await d.page.locator("#search").waitFor();
  await d.page.evaluate(() => { try { localStorage.clear(); } catch {} });
  await d.page.reload();
  await d.page.locator("#search").waitFor();
  await d.page.getByRole("tab", { name: /Catalog/ }).click();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1500 300 0.1");
}

export async function run(d) {
  const p = d.page;
  await d.cue("hello");
  await d.hover(p.locator("#workspace-nav"));
  await d.sleep(2000);

  await d.cue("pick");
  await d.click(p.locator("#search"));
  await d.type("26.04", 150);
  await d.sleep(800);
  await d.click(p.locator(`tr.row[data-vm="${DESKTOP}"] .profile-text`));
  await d.sleep(1500);
  await d.click(p.locator("#profile-mine"));
  await d.sleep(800);

  await d.cue("install");
  await d.click(p.getByRole("button", { name: "Install automatically" }));
  await d.sleep(2500);
  await d.cue("log");
  await d.click(p.locator("#job-bar-log"));
  await d.sleep(4000);
  d.ff(60);
  for (let i = 0; i < 5400; i++) {
    const s = d.vm(`cd ${DEMO} && grep -q '"verify"' artifacts/${DESKTOP}/state.json 2>/dev/null && ! pgrep -f 'vmct[l] bootstrap-unattended' >/dev/null && echo done || echo wait`).trim();
    if (s === "done") break;
    await d.sleep(1000);
  }
  // The desktop's session settles after the post-install: the preview must show GNOME, not GDM.
  await d.sleep(20000);
  d.ffEnd();
  await d.click(p.locator("#log-dialog [data-close]"));
  await d.click(p.locator("#search-clear"));
  await d.click(p.getByRole("tab", { name: /My VMs/ }));
  await d.sleep(1500);

  await d.cue("done");
  await d.hover(p.locator(`#rows tr[data-vm="${DESKTOP}"] .profile-text`));
  await d.sleep(2500);

  await d.cue("preview");
  await d.hover(iconOf(p, DESKTOP).locator(".catalog-icon"));
  await p.locator("#hover-shot").waitFor({ state: "visible", timeout: 15000 });
  await d.sleep(5000);
  await d.hover(p.locator("#details .profile-title"));
  await d.sleep(600);

  await d.cue("console");
  await d.click(p.getByRole("button", { name: /Open console/ }));
  await p.locator("#vnc-screen canvas").waitFor({ timeout: 30000 });
  await d.sleep(4000);
  await d.hover(p.locator("#vnc-keyboard"));
  await d.sleep(800);
  await d.hover(p.locator("#vnc-files"));
  await d.sleep(800);
  await d.hover(p.locator("#vnc-screenshot"));
  await d.sleep(1500);
  await d.click(p.locator("#vnc-close"));
  await d.sleep(800);

  await d.cue("shell");
  await d.openTerminal();
  await d.run(`vmctl shell ${DESKTOP} -- 'hostnamectl | head -4; uptime'`);
  await d.sleep(4500);
  await d.focusBrowser();
  await p.bringToFront();

  await d.cue("two");
  await d.click(p.getByRole("tab", { name: /Catalog/ }));
  await d.click(p.locator("#search"));
  await d.type("ubuntu-2", 120);
  await d.sleep(800);
  await d.click(iconOf(p, DESKTOP));
  await d.sleep(500);
  await d.click(iconOf(p, SERVER));
  await d.sleep(1200);
  await d.click(p.locator("#selection-console"));
  const multi = await d.newestPage();
  d.page = multi;

  await d.cue("multi");
  await multi.locator(".pane iframe").first().waitFor({ timeout: 60000 });
  await multi.locator(".pane iframe").nth(1).waitFor({ timeout: 120000 });
  await d.sleep(6000);
  await d.click(multi.locator("#network-toggle"));
  await d.click(multi.locator("#confirm-yes"));
  await multi.locator("#network-status.linked").waitFor({ timeout: 120000 });
  await d.sleep(2000);

  await d.cue("info");
  await d.click(multi.locator("#network-info"));
  await d.sleep(4000);
  await d.key("Escape");
  await d.sleep(500);

  await d.cue("map");
  const target = d.vm(`cd ${DEMO} && python3 -c "import json; r=json.load(open('artifacts/labs/links/session.json')); print(r['members']['${DESKTOP}']['address'].split('/')[0])"`).trim();
  await d.focusTerminal();
  await d.run(`vmctl shell ${SERVER} -- ping -c 45 -i 0.4 ${target}`, { wait: false });
  await d.focusBrowser();
  await p.bringToFront();
  d.page = p;
  await d.click(p.getByRole("tab", { name: /Labs/ }));
  await d.sleep(1000);
  await d.click(p.locator('section.lab[data-lab="link:session"] [data-map]'));
  const map = await d.newestPage();
  d.page = map;
  await map.locator("#map-live").filter({ hasText: /^Live/ }).waitFor({ timeout: 30000 });
  await d.sleep(1500);
  // The private segment sits below the host and the NAT: scroll to it, rest on each NIC's traffic
  // (the first take hovered the NAT label at the top, and the ping's cables stayed off screen).
  await map.evaluate(() => document.querySelector(".segment-track")?.scrollIntoView({ block: "center", behavior: "smooth" }));
  await d.sleep(2500);
  await d.hover(map.locator(".nic.lan-nic .traffic-label").first());
  await d.sleep(3500);
  await d.hover(map.locator(".nic.lan-nic .traffic-label").nth(1));
  await d.sleep(3500);
  await multi.close().catch(() => {});
  await map.close();
  d.page = p;
  await p.bringToFront();

  await d.cue("power");
  // In the Catalog: the server is not starred, so My VMs drops it the moment it is off (the
  // rehearsal of 2026-10-06 waited there for a row that was gone).
  await d.click(p.getByRole("tab", { name: /Catalog/ }));
  await d.sleep(800);
  if (await p.locator("#search-clear").isVisible()) await d.click(p.locator("#search-clear"));
  await d.click(p.locator("#search"));
  await d.type("ubuntu-2", 120);
  await d.sleep(1200);
  const row = p.locator(`#rows tr[data-vm="${SERVER}"]`);
  const b = await d.box(powerOf(p, SERVER));
  d.vm(`DISPLAY=:0 ~/lab/video/mv.py ${b.x} ${b.y} 0.7 && DISPLAY=:0 xdotool mousedown 1 && sleep 2.6 && DISPLAY=:0 xdotool mouseup 1`);
  for (let i = 0; i < 60 && (await p.locator(`#rows tr[data-vm="${SERVER}"].on`).count()); i++) await d.sleep(1000);
  await d.sleep(1500);
  await d.hover(row.locator(".profile-text"));
  await d.sleep(1200);

  await d.cue("again");
  await d.click(powerOf(p, SERVER));
  for (let i = 0; i < 90 && !(await p.locator(`#rows tr[data-vm="${SERVER}"].on`).count()); i++) await d.sleep(1000);
  await d.sleep(4500);

  await d.cue("end");
  await d.hover(p.locator("#workspace-nav"));
  await d.sleep(3000);
}
