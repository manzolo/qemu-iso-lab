export const title = { en: "2 · Install qemu-iso-lab", it: "2 · Installare qemu-iso-lab" };

export const cues = [
  { id: "intro", en: "To try it you need a Linux computer with KVM, git and Python.",
    it: "Per provarlo serve un computer Linux con KVM, git e Python." },
  { id: "clone", en: "Clone the repository…",
    it: "Si clona il repository…" },
  { id: "setup", en: "…and run setup: it links the vmctl command and checks the host.",
    it: "…e si lancia il setup: collega il comando vmctl e controlla l'host." },
  { id: "ask", en: "Whatever is missing is installed only after asking: here, two optional tools.",
    it: "Quello che manca si installa solo dopo una domanda: qui, due strumenti facoltativi." },
  { id: "checked", en: "QEMU, KVM, the installer tools, the firmware: all there.",
    it: "QEMU, KVM, gli strumenti degli installer, il firmware: c'è tutto." },
  { id: "welcome", en: "Setup ends with a short guide to the next steps.",
    it: "Il setup finisce con una breve guida ai passi successivi." },
  { id: "web", en: "Everything goes through one command, vmctl. Its web dashboard runs on your own computer only.",
    it: "Tutto passa da un solo comando, vmctl. La sua dashboard web gira solo sul proprio computer." },
  { id: "identity", en: "The first time, it asks for the user and password your virtual machines will get…",
    it: "La prima volta chiede utente e password che avranno le macchine virtuali…" },
  { id: "private", en: "…kept in a private file of your checkout, never in the repository.",
    it: "…salvati in un file privato della propria copia, mai nel repository." },
  { id: "dashboard", en: "And here is the lab: the whole catalog, ready to install.",
    it: "Ed ecco il lab: tutto il catalogo, pronto da installare." },
  { id: "next", en: "Next: the first virtual machine.",
    it: "Prossimo passo: la prima macchina virtuale." },
];

export async function setup(d) {
  d.vm("pkill -f 'vmct[l] web'; rm -rf ~/lab/demo/qemu-iso-lab; mkdir -p ~/lab/demo; rm -f /tmp/demo-prompt");
  const pages = d.page.context().pages();
  for (const p of pages.slice(1)) await p.close();
  d.page = pages[0];
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(1500);
  await d.cue("clone");
  await d.run("git clone https://github.com/manzolo/qemu-iso-lab");
  await d.sleep(800);
  await d.run("cd qemu-iso-lab");
  await d.cue("setup");
  await d.run("./setup.sh", { wait: false });
  await d.sleep(4500);
  await d.cue("ask");
  await d.sleep(2500);
  await d.type("y", 200);
  await d.key("Return");
  await d.sleep(6000);
  await d.cue("checked");
  // Setup ends on the welcome screen, which waits for "What now? [1-4]".
  for (let i = 0; i < 300; i++) {
    if (d.vm("pgrep -f 'vmct[l] welcome' >/dev/null && echo y || echo n").trim() === "y") break;
    await d.sleep(1000);
  }
  await d.sleep(1500);
  await d.cue("welcome");
  await d.sleep(3000);
  await d.cue("web");
  await d.sleep(1500);
  await d.type("1", 200);
  await d.key("Return");
  await d.newestPage();
  await d.focusBrowser();
  const p = d.page;
  await p.locator("#identity-user").waitFor({ state: "visible", timeout: 30000 });
  await d.sleep(1200);
  await d.cue("identity");
  await d.click(p.locator("#identity-user"));
  await d.key("ctrl+a");
  await d.type("demo", 160);
  await d.sleep(500);
  await d.click(p.locator("#identity-password"));
  await d.key("ctrl+a");
  await d.type("demo", 160);
  await d.sleep(800);
  await d.cue("private");
  await d.hover(p.locator("#identity-store"));
  await d.sleep(1500);
  await d.click(p.locator("#identity-save"));
  await p.locator("#identity-dialog").waitFor({ state: "hidden", timeout: 30000 });
  await d.cue("dashboard");
  await d.sleep(1200);
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 700 450 1.2");
  await d.sleep(1500);
  await d.cue("next");
}
