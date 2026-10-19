// Clip 8 (2026-10-19, Manzolo: "il trascinamento della rete"): two running machines joined by
// dragging the RJ45 socket of one row onto the other (index.html's pointer drag, 2026-10-18), the
// hot-plugged NIC checked with a ping, the session lab and its map, then the same drag unlinking.
// The drag is the real X pointer: mousedown on the socket, a glide to the other row, mouseup.
// Needs: ubuntu-24.04-cloud and debian-12-cloud installed in the demo checkout (setup starts them).
const FIRST = "ubuntu-24.04-cloud";
const SECOND = "debian-12-cloud";
const DEMO = "~/lab/demo/qemu-iso-lab";

export const title = { en: "8 · Link two machines with a cable", it: "8 · Collegare due macchine con un cavo" };

export const cues = [
  { id: "two", en: "Two machines are running: an Ubuntu server and a Debian one. Each has its own network: they cannot see each other.",
    it: "Due macchine accese: un server Ubuntu e uno Debian. Ognuna ha la sua rete: non si vedono." },
  { id: "grab", en: "Every row has a network socket. Grab it and drag: a cable follows the pointer.",
    it: "Ogni riga ha una presa di rete. La si afferra e si trascina: un cavo segue il puntatore." },
  { id: "drop", en: "Drop it on the other machine: vmctl asks first, and shows the command it will run.",
    it: "La si lascia sull'altra macchina: vmctl chiede conferma e mostra il comando che eseguirà." },
  { id: "linked", en: "Both get a new network card on a private segment, while they run, each with its own address.",
    it: "Entrambe ricevono una nuova scheda di rete su un segmento privato, a caldo, ognuna con il suo indirizzo." },
  { id: "status", en: "From a terminal, link status shows the segment and the addresses…",
    it: "Da un terminale, link status mostra il segmento e gli indirizzi…" },
  { id: "ping", en: "…and the Ubuntu server reaches the Debian one.",
    it: "…e il server Ubuntu raggiunge quello Debian." },
  { id: "lab", en: "The link is a lab of its own now: its card, and its map with the cable lit by the traffic.",
    it: "Ora il collegamento è un lab a sé: la sua scheda, e la sua mappa con il cavo acceso dal traffico." },
  { id: "unlink", en: "Dragging the socket onto the same machine again unlinks them.",
    it: "Trascinando di nuovo la presa sulla stessa macchina, le si scollega." },
  { id: "end", en: "Nothing is saved in the profiles: the link lasts until you take it away.",
    it: "Nei profili non si salva nulla: il collegamento dura finché non lo si toglie." },
];

const row = (p, vm) => p.locator(`#rows tr[data-vm="${vm}"]`);
const socket = (p, vm) => row(p, vm).locator(".link-handle");

export async function setup(d) {
  // Both machines running headless (the hot-plug needs QMP), no link left from an earlier take,
  // My VMs holding exactly the two of them.
  d.vm(`cd ${DEMO} && ./bin/vmctl link --off --segment session >/dev/null 2>&1; ` +
       `./bin/vmctl catalog clear >/dev/null 2>&1; ./bin/vmctl catalog add ${FIRST} ${SECOND} >/dev/null 2>&1; ` +
       `for v in ${FIRST} ${SECOND}; do ./bin/vmctl start $v --headless --background >/dev/null 2>&1; done; ` +
       `for v in ${FIRST} ${SECOND}; do for i in $(seq 1 60); do ./bin/vmctl shell $v -- true >/dev/null 2>&1 && break; sleep 2; done; done; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.focusBrowser();
  await d.page.bringToFront();
  await d.page.goto(d.restartWeb());
  await d.page.locator("#search").waitFor();
  await d.page.locator("#rows tr[data-vm]").first().waitFor({ timeout: 30000 });
  await d.page.getByRole("tab", { name: /My VMs/ }).click();
  await socket(d.page, SECOND).waitFor({ timeout: 30000 });
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1500 300 0.1");
}

// The real pointer drags the source's socket onto the target's row (the page listens to pointer
// events; a synthetic Playwright drag would not move the cable the viewer sees).
async function dragSocket(d, source, target) {
  const p = d.page;
  await d.hover(socket(p, source));
  await d.sleep(600);
  const from = await d.box(socket(p, source));
  d.vm("DISPLAY=:0 xdotool mousedown 1");
  await d.sleep(400);
  // A wide swing first: the two rows are neighbours and a straight drop would draw a stub of cable.
  d.vm(`DISPLAY=:0 ~/lab/video/mv.py ${from.x + 260} ${from.y + 170} 1.3`);
  await d.sleep(700);
  const to = await d.box(row(p, target).locator(".profile-text"));
  d.vm(`DISPLAY=:0 ~/lab/video/mv.py ${to.x} ${to.y} 1.5`);
  await d.sleep(1500);
  d.vm("DISPLAY=:0 xdotool mouseup 1");
}

// The confirmation dialog of a link or an unlink: its primary button.
async function confirm(d, label) {
  const button = d.page.locator("dialog[open] button", { hasText: label }).first();
  await button.waitFor({ timeout: 15000 });
  await d.sleep(2500);
  await d.click(button);
}

export async function run(d) {
  const p = d.page;
  await d.cue("two");
  await d.hover(row(p, FIRST).locator(".profile-text"));
  await d.sleep(1500);
  await d.hover(row(p, SECOND).locator(".profile-text"));
  await d.sleep(1500);

  await d.cue("grab");
  await d.hover(socket(p, FIRST));
  await d.sleep(1500);
  await dragSocket(d, FIRST, SECOND);

  await d.cue("drop");
  await confirm(d, "Link them");
  await d.sleep(1500);

  await d.cue("linked");
  for (let i = 0; i < 60; i++) {
    const s = d.vm(`cd ${DEMO} && ./bin/vmctl link --status 2>/dev/null | grep -c 192.168.100 || true`).trim();
    if (Number(s) >= 2) break;
    await d.sleep(1000);
  }
  await d.sleep(3000);
  await d.hover(row(p, FIRST).locator(".link-handle"));
  await d.sleep(2500);

  await d.cue("status");
  await d.openTerminal();
  await d.run("vmctl link --status");
  await d.sleep(3500);
  await d.cue("ping");
  await d.run(`vmctl shell ${FIRST} -- ping -c 3 192.168.100.2`);
  await d.sleep(3000);
  await d.focusBrowser();
  await p.bringToFront();

  await d.cue("lab");
  await d.click(p.getByRole("tab", { name: /Labs/ }));
  const card = p.locator('section.lab[data-lab="link:session"]');
  await card.waitFor({ timeout: 15000 });
  await card.scrollIntoViewIfNeeded();
  await d.hover(card.locator(".members"));
  await d.sleep(2000);
  // Traffic on the cable while the map is open: a ping in the background, off screen.
  d.vm(`cd ${DEMO} && (setsid ./bin/vmctl shell ${FIRST} -- ping -c 30 -i 0.5 192.168.100.2 >/dev/null 2>&1 &) ; true`);
  await d.click(card.locator("[data-map]"));
  const map = await d.newestPage();
  await map.locator("#map-live").filter({ hasText: /^Live/ }).waitFor({ timeout: 30000 });
  await map.evaluate(() => document.querySelector(".segment-track")?.scrollIntoView({ block: "center", behavior: "smooth" }));
  await d.sleep(2000);
  d.page = map;
  await d.hover(map.locator(`.nic.lan-nic[data-vm="${FIRST}"] .traffic-label`).first());
  await d.sleep(2500);
  await d.hover(map.locator(`.nic.lan-nic[data-vm="${SECOND}"] .traffic-label`).first());
  await d.sleep(2500);
  await map.close();
  d.page = p;
  await p.bringToFront();

  await d.cue("unlink");
  await d.click(p.getByRole("tab", { name: /My VMs/ }));
  await socket(p, FIRST).waitFor();
  await d.sleep(800);
  await dragSocket(d, FIRST, SECOND);
  const asked = p.locator("dialog[open] button", { hasText: /Unlink/ }).first();
  if (await asked.isVisible({ timeout: 4000 }).catch(() => false)) await confirm(d, /Unlink/);
  for (let i = 0; i < 60; i++) {
    const s = d.vm(`cd ${DEMO} && ./bin/vmctl link --status 2>/dev/null | grep -c 192.168.100 || true`).trim();
    if (Number(s) === 0) break;
    await d.sleep(1000);
  }
  await d.sleep(2000);

  await d.cue("end");
  await d.hover(row(p, SECOND).locator(".profile-text"));
  await d.sleep(3000);
}
