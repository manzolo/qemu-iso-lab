export const title = { en: "3 · The first virtual machine", it: "3 · La prima macchina virtuale" };

const VMNAME = "ubuntu-24.04-cloud";
const ART = `~/lab/demo/qemu-iso-lab/artifacts/${VMNAME}`;

export const cues = [
  { id: "intro", en: "A first machine: a small Ubuntu 24.04 server, built from the official cloud image.",
    it: "Una prima macchina: un piccolo server Ubuntu 24.04, costruito dalla cloud image ufficiale." },
  { id: "details", en: "Its card says what it needs: memory, CPUs, disk, and how it installs.",
    it: "La sua scheda dice cosa le serve: memoria, CPU, disco e come si installa." },
  { id: "star", en: "The star adds it to My VMs, your own short list.",
    it: "La stellina la aggiunge a My VMs, la propria lista." },
  { id: "install", en: "Install automatically: no questions, no installer to click through.",
    it: "Install automatically: nessuna domanda, nessun installer da cliccare." },
  { id: "log", en: "Every action is a job with a live log, and it keeps running if you close the page.",
    it: "Ogni azione è un job con il suo log dal vivo, e continua anche chiudendo la pagina." },
  { id: "steps", en: "It downloads the image and checks it, boots it once with cloud-init, then logs in over SSH to verify it.",
    it: "Scarica l'immagine e la controlla, la avvia una volta con cloud-init, poi entra via SSH per verificarla." },
  { id: "done", en: "Done: installed and verified.",
    it: "Fatto: installata e verificata." },
  { id: "mine", en: "It is in My VMs, and already running.",
    it: "È in My VMs, ed è già accesa." },
  { id: "next", en: "Next: its console, in the browser.",
    it: "Prossimo passo: la sua console, nel browser." },
];

export async function setup(d) {
  d.vm(`cd ~/lab/demo/qemu-iso-lab && ./bin/vmctl stop ${VMNAME} >/dev/null 2>&1; ./bin/vmctl catalog clear >/dev/null 2>&1; ` +
       `rm -rf ${ART} isos/.cloudimg isos/ubuntu-24.04-minimal-cloudimg-amd64.img; true`);
  await d.focusBrowser();
  await d.page.bringToFront();
  await d.page.goto(d.restartWeb());
  await d.page.locator("#search").waitFor();
  await d.page.getByRole("button", { name: "Catalog", exact: true }).first().click();
  if (await d.page.locator("#search-clear").isVisible()) await d.page.locator("#search-clear").click();
  await d.page.locator("#log-dialog").evaluate((x) => x.open && x.close());
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1500 300 0.1");
}

export async function run(d) {
  const p = d.page;
  await d.cue("intro");
  await d.click(p.locator("#search"));
  await d.type("cloud", 150);
  await d.sleep(800);
  await d.click(p.locator(`tr.row[data-vm="${VMNAME}"]`));
  await d.sleep(800);
  await d.cue("details");
  await d.hover(p.locator("#details"));
  await d.sleep(1500);
  await d.cue("star");
  await d.click(p.locator("#profile-mine"));
  await d.sleep(1500);
  await d.cue("install");
  await d.click(p.getByRole("button", { name: "Install automatically" }));
  await d.sleep(2500);
  await d.cue("log");
  await d.click(p.locator("#job-bar-log"));
  await d.sleep(3000);
  await d.cue("steps");
  await d.sleep(3000);
  d.ff(12);
  for (let i = 0; i < 900; i++) {
    const s = d.vm(`grep -q '"verify"' ${ART}/state.json 2>/dev/null && ! pgrep -f 'vmct[l] bootstrap-cloudimg' >/dev/null && echo done || echo wait`).trim();
    if (s === "done") break;
    await d.sleep(1000);
  }
  await d.sleep(2000);
  d.ffEnd();
  await d.sleep(1500);
  await d.cue("done");
  await d.sleep(1500);
  await d.click(p.locator("#log-dialog [data-close]"));
  await d.sleep(1200);
  await d.hover(p.locator("#details"));
  await d.sleep(1500);
  await d.cue("mine");
  await d.click(p.locator("#search-clear"));
  await d.click(p.getByRole("button", { name: "My VMs" }).first());
  await d.sleep(2000);
  await d.cue("next");
}
