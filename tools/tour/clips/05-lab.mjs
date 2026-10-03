export const title = { en: "5 · A lab: two machines, one network", it: "5 · Un lab: due macchine, una rete" };

const LAB = "vpn-lab";
const MEMBERS = ["vpn-lab-server", "vpn-lab-client"];
const CHECKOUT = "~/lab/demo/qemu-iso-lab";

export const cues = [
  { id: "labs", en: "Machines can also come as labs: here two VMs on their own private network, a VPN server and its client.",
    it: "Le macchine possono arrivare anche come lab: qui due VM sulla loro rete privata, un server VPN e il suo client." },
  { id: "install", en: "Install lab installs every member, in order.",
    it: "Install lab installa tutti i membri, in ordine." },
  { id: "wait", en: "Two cloud images, provisioned and verified one after the other.",
    it: "Due cloud image, preparate e verificate una dopo l'altra." },
  { id: "up", en: "Both are running, each with its address on the lab network.",
    it: "Sono accese tutte e due, ognuna con il suo indirizzo sulla rete del lab." },
  { id: "consoles", en: "Consoles opens them side by side, in one page.",
    it: "Consoles le apre affiancate, in una sola pagina." },
  { id: "guide", en: "The guide walks through the exercises, in English and Italian…",
    it: "La guida accompagna negli esercizi, in inglese e in italiano…" },
  { id: "tests", en: "…and Run tests checks, inside the machines, that every exercise really works.",
    it: "…e Run tests controlla, dentro le macchine, che ogni esercizio funzioni davvero." },
  { id: "result", en: "Every test script passed.",
    it: "Tutti gli script di test sono passati." },
  { id: "end", en: "That is qemu-iso-lab: pick a machine, or a lab, from the catalog and try it.",
    it: "Questo è qemu-iso-lab: si sceglie una macchina, o un lab, dal catalogo e la si prova." },
];

export async function setup(d) {
  d.vm("pkill -f 'vmct[l] group (install|test)'; pkill -f 'tui_job[s].py'; sleep 2; true");
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl stop ubuntu-24.04-cloud >/dev/null 2>&1; ` +
       `for v in ${MEMBERS.join(" ")}; do ./bin/vmctl stop $v >/dev/null 2>&1; rm -rf artifacts/$v; done; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.focusBrowser();
  await d.page.bringToFront();
  await d.page.keyboard.press("Escape").catch(() => {});
  await d.page.goto(d.restartWeb());
  await d.page.locator("#search").waitFor();
  await d.page.locator("#vnc-dialog").evaluate((x) => x.open && x.close()).catch(() => {});
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1500 300 0.1");
}

async function waitJob(d, pattern, extra = "true") {
  for (let i = 0; i < 1800; i++) {
    const s = d.vm(`if pgrep -f '${pattern}' >/dev/null; then echo busy; elif ${extra}; then echo done; else echo busy; fi`).trim();
    if (s === "done") return;
    await d.sleep(1000);
  }
  throw new Error(`job still running: ${pattern}`);
}

async function confirmIfAsked(d) {
  await d.sleep(700);
  const ok = d.page.locator("dialog[open] #cmd-run, dialog[open] button:has-text('Run it'), dialog[open] button.primary:has-text('Run')").first();
  if (await ok.isVisible().catch(() => false)) await d.click(ok);
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
  await d.sleep(1200);

  await d.cue("install");
  await d.click(card().locator(".lab-footer .buttons button").first());
  await confirmIfAsked(d);
  await d.sleep(2000);
  await d.click(p.locator("#job-bar-log"));
  await d.sleep(2500);
  await d.cue("wait");
  await d.sleep(2500);
  d.ff(16);
  await waitJob(d, "vmct[l] group install", `grep -q verify ${CHECKOUT}/artifacts/vpn-lab-client/state.json`);
  await d.sleep(1500);
  d.ffEnd();
  await d.sleep(1500);
  await d.click(p.locator("#log-dialog [data-close]"));
  await d.sleep(1500);

  await d.cue("up");
  await card().scrollIntoViewIfNeeded();
  await d.hover(card().locator(".member-address").first());
  await d.sleep(1200);
  await d.hover(card().locator(".member-address").nth(1));
  await d.sleep(1200);

  await d.cue("consoles");
  await d.click(card().locator("[data-lab-consoles]"));
  const multi = await d.newestPage();
  await multi.waitForTimeout(9000);
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 400 300 1.0");
  await d.sleep(2500);
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1200 300 1.0");
  await d.sleep(2500);
  await multi.close();
  d.page = p;
  await p.bringToFront();
  await d.sleep(800);

  await d.cue("guide");
  await d.click(card().locator("[data-guide]"));
  const guide = await d.newestPage();
  await guide.waitForTimeout(1500);
  await d.scroll(1400, 3500);
  await d.sleep(1500);
  await guide.close();
  d.page = p;
  await p.bringToFront();
  await d.sleep(800);

  await d.cue("tests");
  await d.click(card().getByRole("button", { name: "Run tests" }));
  await confirmIfAsked(d);
  await d.sleep(1500);
  await d.click(p.locator("#job-bar-log"));
  await d.sleep(2000);
  d.ff(4);
  await waitJob(d, "vmct[l] group test");
  await d.sleep(800);
  d.ffEnd();
  await d.cue("result");
  await d.sleep(2500);
  await d.click(p.locator("#log-dialog [data-close]"));
  await d.sleep(800);
  await d.cue("end");
}
