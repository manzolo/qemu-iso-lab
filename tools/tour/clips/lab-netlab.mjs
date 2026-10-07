export const title = { en: "A router, a DNS and a client: the network lab", it: "Un router, un DNS e un client: il lab di rete" };
export const series = "labs";
export const lab = "netlab";

const LAB = "netlab";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";

export const cues = [
  { id: "intro", en: "A small network like a home or an office: a pfSense router in front, Pi-hole serving DNS and DHCP on the LAN, and a Lubuntu desktop as the user. The network lab builds the three machines and wires them.",
    it: "Una piccola rete come quella di casa o di un ufficio: un router pfSense davanti, Pi-hole che fa DNS e DHCP sulla LAN, e un desktop Lubuntu come utente. Il lab di rete costruisce le tre macchine e le collega." },
  { id: "install", en: "This install is the long one: pfSense from its ISO, then Pi-hole and the desktop from Ubuntu's autoinstall. Half an hour, unattended.",
    it: "Questa è l'installazione lunga: pfSense dalla sua ISO, poi Pi-hole e il desktop con l'autoinstall di Ubuntu. Mezz'ora, senza domande." },
  { id: "pihole", en: "On Pi-hole: FTL answers DNS on port 53, blocking is enabled, and it is the LAN's DHCP server with its own address range. pfSense hands the clients to it and asks it for its own names.",
    it: "Su Pi-hole: FTL risponde al DNS sulla porta 53, il blocco è attivo, ed è il server DHCP della LAN con il suo intervallo di indirizzi. pfSense gli affida i client e gli chiede i propri nomi." },
  { id: "client", en: "On the desktop: its DNS is Pi-hole, 192.168.0.10, in the domain qlan. pfsense.qlan resolves to the router, and a public name is answered by Pi-hole through pfSense.",
    it: "Sul desktop: il suo DNS è Pi-hole, 192.168.0.10, nel dominio qlan. pfsense.qlan risolve nel router, e un nome pubblico è risposto da Pi-hole attraverso pfSense." },
  { id: "route", en: "The client reaches the Internet only through the router: its default route is 192.168.0.1, and pfSense translates addresses on its WAN.",
    it: "Il client raggiunge Internet solo attraverso il router: la sua rotta di default è 192.168.0.1, e pfSense traduce gli indirizzi sulla sua WAN." },
  { id: "pfsense", en: "pfSense itself, over SSH: vtnet1 is the LAN side at 192.168.0.1; the routing table sends everything else out of the WAN.",
    it: "pfSense stesso, via SSH: vtnet1 è il lato LAN a 192.168.0.1; la tabella di routing manda tutto il resto fuori dalla WAN." },
  { id: "forwards", en: "From the host, the lab is reached through the router's forwards: the pfSense GUI, the Pi-hole UI and SSH to each member. vmctl lab check verifies all five.",
    it: "Dall'host, il lab si raggiunge attraverso i forward del router: la GUI di pfSense, l'interfaccia di Pi-hole e l'SSH di ogni membro. vmctl lab check li verifica tutti e cinque." },
  { id: "map", en: "The network map draws what you just read: the segment, the addresses, the forwards and the exercises.",
    it: "La mappa di rete disegna quello che hai appena letto: il segmento, gli indirizzi, i forward e gli esercizi." },
  { id: "tests", en: "The lab's tests check the same things over SSH, on all three members.",
    it: "I test del lab controllano le stesse cose via SSH, su tutti e tre i membri." },
  { id: "end", top: true, en: "The guide, in English and Italian, walks through every member from the lab's card.",
    it: "La guida, in inglese e in italiano, accompagna in ogni membro dalla card del lab." },
];

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && for v in lubuntu-lab pihole-lab pfsense-lab; do ./bin/vmctl stop $v >/dev/null 2>&1; done; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page) await p.close();  // a stray about:blank would be taken for the map's tab
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab");
  await d.sleep(3500);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(6000);
  d.ff(60);
  for (let i = 0; i < 3600 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(2000);

  await d.cue("pihole");
  await d.run("clear");
  await d.session("pihole-lab");
  await d.guest("sudo pihole status", { read: 4000 });
  await d.guest("sudo pihole-FTL --config dhcp.active; sudo pihole-FTL --config dhcp.start; sudo pihole-FTL --config dhcp.end", { read: 5000 });
  await d.leave();

  await d.cue("client");
  await d.session("lubuntu-lab");
  await d.guest("resolvectl status | grep -A3 'Link.*enp'", { read: 4000 });
  await d.guest("resolvectl query pfsense.qlan", { read: 3500 });
  await d.guest("resolvectl query example.org", { read: 3500, timeout: 30000 });
  await d.cue("route");
  await d.guest("ip route | head -1", { read: 3000 });
  await d.guest("curl -sI https://example.org | head -1    # through the pfSense NAT", { read: 3500, timeout: 30000 });
  await d.leave();

  await d.cue("pfsense");
  await d.run("clear");
  await d.run("vmctl shell pfsense-lab -- 'ifconfig vtnet1 | grep inet'", { timeout: 60000 });
  await d.sleep(3000);
  await d.run("vmctl shell pfsense-lab -- 'netstat -rn | head -5'", { timeout: 60000 });
  await d.sleep(4000);

  await d.cue("forwards");
  await d.run("vmctl lab check", { timeout: 300000 });
  await d.sleep(5000);

  await d.cue("map");
  await d.run(`vmctl group map ${LAB} --open`, { timeout: 60000 });
  const page = await d.newestPage();
  await d.focusBrowser();
  await page.waitForTimeout(1500);
  await d.scroll(500, 2500);
  await d.sleep(3500);
  await page.close();
  await d.focusTerminal();

  await d.cue("tests");
  await d.run("clear");
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
