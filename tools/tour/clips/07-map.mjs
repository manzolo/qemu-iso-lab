// Clip 7 (v0.22.0): the lab map with live traffic and the packet inspector, on the netlab. Needs the
// netlab installed and up in the demo checkout (vmctl group install netlab): pfSense, Pi-hole and
// the client on the lab-lan segment. The HTTP request is made from the host terminal with
// `vmctl shell`, so the viewer sees both the command and the frames it leaves on the cable.
const LAB = "netlab";
const CLIENT = "lubuntu-lab";
const REQUEST = "vmctl shell lubuntu-lab -- curl -sI http://192.168.0.10/admin/";

export const title = { en: "7 · The map: live traffic and packets", it: "7 · La mappa: traffico e pacchetti dal vivo" };

export const cues = [
  { id: "labs", en: "A running lab is also a network. This is the netlab: a pfSense router, a Pi-hole and a client on a private segment.",
    it: "Un lab acceso è anche una rete. Questo è il netlab: un router pfSense, un Pi-hole e un client su un segmento privato." },
  { id: "map", en: "Map draws the topology live: the host with its forwards, the machines, the isolated segment below.",
    it: "Map disegna la topologia dal vivo: l'host con i suoi inoltri, le macchine, il segmento isolato in basso." },
  { id: "cables", en: "Cables light up only with measured traffic, and every port shows its bytes per second.",
    it: "I cavi si accendono solo con il traffico misurato, e ogni porta mostra i suoi byte al secondo." },
  { id: "lens", en: "The lens on a cable opens the packet inspector: every row is a frame seen on that cable, with protocol, addresses and ports.",
    it: "La lente su un cavo apre l'ispettore dei pacchetti: ogni riga è un frame visto su quel cavo, con protocollo, indirizzi e porte." },
  { id: "request", en: "Let us make a web request from the client to the Pi-hole, from the host terminal.",
    it: "Facciamo una richiesta web dal client al Pi-hole, dal terminale dell'host." },
  { id: "appear", en: "There it is on the cable: TCP towards port 80, as it happens.",
    it: "Eccola sul cavo: TCP verso la porta ottanta, mentre succede." },
  { id: "filter", en: "The filters narrow the list: only TCP, or only what mentions port 80.",
    it: "I filtri restringono la lista: solo TCP, o solo ciò che riguarda la porta ottanta." },
  { id: "pause", en: "Pause freezes the list; the capture goes on.",
    it: "Pausa ferma la lista; la cattura continua." },
  { id: "detail", en: "A click on a row opens the packet: Ethernet, IP, TCP, field by field, and the bytes of its headers. The content is never recorded.",
    it: "Un clic sulla riga apre il pacchetto: Ethernet, IP, TCP, campo per campo, e i byte delle intestazioni. Il contenuto non viene mai registrato." },
  { id: "end", en: "The same for every lab, and for two machines joined with vmctl link.",
    it: "Lo stesso per ogni lab, e per due macchine unite con vmctl link." },
];

export async function setup(d) {
  d.vm("pkill -f 'vmct[l] group (install|test)'; pkill -f 'tui_job[s].py'; true");
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.focusBrowser();
  await d.page.bringToFront();
  await d.page.keyboard.press("Escape").catch(() => {});
  await d.page.goto(d.restartWeb());
  await d.page.locator("#search").waitFor();
  await d.page.locator("#vnc-dialog").evaluate((x) => x.open && x.close()).catch(() => {});
  await d.page.locator("#log-dialog").evaluate((x) => x.open && x.close()).catch(() => {});
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1500 300 0.1");
}

export async function run(d) {
  const p = d.page;
  const card = () => p.locator(`section.lab[data-lab="${LAB}"]`);
  await d.cue("labs");
  await d.click(p.getByRole("button", { name: "Labs", exact: true }).first());
  await d.sleep(1200);
  await card().scrollIntoViewIfNeeded();
  await d.hover(card().locator(".lab-name"));
  await d.sleep(1500);
  await d.hover(card().locator(".members"));
  await d.sleep(1500);

  await d.cue("map");
  await d.click(card().locator("[data-map]"));
  const map = await d.newestPage();
  d.page = map;
  await map.locator("#map-live").filter({ hasText: /^Live/ }).waitFor({ timeout: 30000 });
  await map.evaluate(() => document.querySelector(".map-shell")?.scrollIntoView({ block: "start" }));
  await d.sleep(1200);
  await d.hover(map.locator(".node.host-n"));
  await d.sleep(1200);
  await d.hover(map.locator(".vm").nth(1));
  await d.sleep(1200);
  await d.hover(map.locator(".segment-track"));
  await d.sleep(1500);

  await d.cue("cables");
  const cable = map.locator(`.nic.lan-nic[data-vm="${CLIENT}"]`);
  await cable.scrollIntoViewIfNeeded();
  await d.hover(cable.locator(".traffic-label"));
  await d.sleep(3500);

  await d.cue("lens");
  await d.click(cable.locator(".inspect-btn"));
  await map.locator("#packet-inspector").waitFor({ state: "visible" });
  await d.sleep(1500);
  // Live rows are rebuilt as frames arrive: the pointer rests on the table, never on one row.
  await d.hover(map.locator("#packet-inspector thead"));
  await d.sleep(2500);

  await d.cue("request");
  await d.openTerminal();
  await d.sleep(800);
  await d.run(REQUEST);
  await d.sleep(2500);
  await d.focusBrowser();
  await map.bringToFront();

  await d.cue("appear");
  const http = map.locator("tr.packet-row", { hasText: ":80" });
  await http.first().waitFor({ timeout: 20000 });
  await d.hover(map.locator("#packet-rows"));
  await d.sleep(3000);

  await d.cue("filter");
  await d.click(map.locator('.chip[data-proto="TCP"]'));
  await d.sleep(1800);
  await d.click(map.locator("#packet-search"));
  await d.type("80", 160);
  await d.sleep(2500);

  await d.cue("pause");
  await d.click(map.locator("#packet-pause"));
  await d.sleep(2000);

  await d.cue("detail");
  const row = map.locator("tr.packet-row", { hasText: /PSH|SYN/ }).first();
  await d.click(row);
  await map.locator("tr.packet-detail-row").waitFor();
  await map.locator("tr.packet-detail-row").scrollIntoViewIfNeeded();
  await d.sleep(1500);
  await d.hover(map.locator(".packet-layers section").nth(1));
  await d.sleep(2000);
  await d.hover(map.locator(".packet-layers section").nth(2));
  await d.sleep(2000);
  await d.hover(map.locator(".packet-hex pre"));
  await d.sleep(3000);

  await d.cue("end");
  await d.sleep(2500);
  d.page = p;
}
