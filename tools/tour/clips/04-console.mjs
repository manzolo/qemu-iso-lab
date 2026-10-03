export const title = { en: "4 · The console in the browser", it: "4 · La console nel browser" };

const VMNAME = "ubuntu-24.04-cloud";

export const cues = [
  { id: "open", en: "The machine is running. Open console shows its screen right in the browser.",
    it: "La macchina è accesa. Open console mostra il suo schermo direttamente nel browser." },
  { id: "login", en: "It is a real screen and keyboard: log in with the user chosen at the start.",
    it: "È uno schermo con una tastiera vera: si entra con l'utente scelto all'inizio." },
  { id: "keyboard", en: "Keyboard sends the combinations the browser would keep for itself: Ctrl+Alt+Del, function keys, the other terminals.",
    it: "Keyboard manda le combinazioni che il browser si terrebbe: Ctrl+Alt+Canc, i tasti funzione, gli altri terminali." },
  { id: "ssh", en: "SSH opens a real terminal next to the screen, with the project's own key: no password.",
    it: "SSH apre un vero terminale accanto allo schermo, con la chiave del progetto: senza password." },
  { id: "files", en: "Files browses the machine's folders: drop files to upload them, click to download.",
    it: "Files sfoglia le cartelle della macchina: si trascinano i file per caricarli, si clicca per scaricarli." },
  { id: "more", en: "Screenshot, recording, and the console in its own window are one click away.",
    it: "Screenshot, registrazione e la console in una finestra a parte sono a un clic." },
  { id: "next", en: "Next: a lab with two machines on their own network.",
    it: "Prossimo passo: un lab con due macchine sulla loro rete." },
];

export async function setup(d) {
  // The console starts at the login prompt again: end the tty1 session of an earlier take.
  d.vm(`cd ~/lab/demo/qemu-iso-lab && ./bin/vmctl shell ${VMNAME} -- sudo pkill -KILL -t tty1 >/dev/null 2>&1; true`);
  await d.focusBrowser();
  await d.page.bringToFront();
  await d.page.goto(d.restartWeb());
  await d.page.locator("#search").waitFor();
  await d.page.locator(`tr.row[data-vm="${VMNAME}"]`).click();
  await d.page.getByRole("button", { name: /Open console/ }).waitFor();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1500 300 0.1");
}

export async function run(d) {
  const p = d.page;
  await d.cue("open");
  await d.click(p.getByRole("button", { name: /Open console/ }));
  await p.locator("#vnc-screen canvas").waitFor({ timeout: 30000 });
  await d.sleep(2500);
  await d.cue("login");
  await d.click(p.locator("#vnc-screen canvas"));
  await d.key("Return");
  await d.sleep(1200);
  await d.type("demo", 160);
  await d.key("Return");
  await d.sleep(1200);
  await d.type("demo", 160);
  await d.key("Return");
  await d.sleep(3000);
  await d.type("hostnamectl | head -4", 70);
  await d.key("Return");
  await d.sleep(2500);

  await d.cue("keyboard");
  await d.click(p.locator("#vnc-keyboard"));
  await d.sleep(1000);
  await d.hover(p.locator("#vnc-cad"));
  await d.sleep(1200);
  await d.hover(p.locator("#console-send-function"));
  await d.sleep(1200);
  await d.hover(p.locator("#console-send-tty"));
  await d.sleep(1200);

  await d.cue("ssh");
  await d.click(p.locator("#console-panel-close"));
  await d.sleep(500);
  await d.click(p.locator("#vnc-ssh"));
  await d.sleep(3500);
  await d.click(p.locator("#console-ssh"));
  await d.type("df -h / && uptime", 70);
  await d.key("Return");
  await d.sleep(3000);

  await d.cue("files");
  await d.click(p.locator("#vnc-files"));
  await d.sleep(3000);
  await d.hover(p.locator("#console-files-drop"));
  await d.sleep(2000);

  await d.cue("more");
  await d.click(p.locator("#console-panel-close"));
  await d.hover(p.locator("#vnc-screenshot"));
  await d.sleep(900);
  await d.hover(p.locator("#vnc-record"));
  await d.sleep(900);
  await d.hover(p.locator("#vnc-detach"));
  await d.sleep(1500);
  await d.cue("next");
}
