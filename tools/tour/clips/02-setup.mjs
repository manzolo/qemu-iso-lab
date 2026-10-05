// Clip 2 since v0.20.0: the one-line installer (README Quick start) instead of clone + ./setup.sh.
// The shell of this clip has HOME=/home/demo (never a real name on screen: the installer prints the
// home in "Cloning into" and the welcome); the directory is created once per studio session by
// setup() through the libvirt guest agent (TOUR_DOMAIN, default lubuntu22-studio), owned by the SSH
// user, and the menu entry goes to the real session's XDG_DATA_HOME so the applications menu shows
// it. Clips 3-6 keep finding the demo at ~/lab/demo/qemu-iso-lab: a symlink to the new checkout.
import { execSync } from "node:child_process";

export const title = { en: "2 · Install qemu-iso-lab", it: "2 · Installare qemu-iso-lab" };

export const cues = [
  { id: "intro", en: "To try it you need a Linux computer with KVM. One line in a terminal is enough.",
    it: "Per provarlo serve un computer Linux con KVM. Basta una riga nel terminale." },
  { id: "line", en: "It fetches the project into your home folder and starts the setup.",
    it: "Scarica il progetto in una cartella della home e avvia il setup." },
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
  { id: "menu", en: "From now on, QEMU ISO Lab is in your applications menu too.",
    it: "E da ora QEMU ISO Lab è anche nel menu delle applicazioni." },
  { id: "next", en: "Next: the first virtual machine.",
    it: "Prossimo passo: la prima macchina virtuale." },
];

const HOME = "/home/demo";
const RC = "~/lab/video/demo-home.bashrc";

export async function setup(d) {
  const domain = process.env.TOUR_DOMAIN || "lubuntu22-studio";
  const user = (process.env.TOUR_VM || "").split("@")[0] || "manzolo";
  execSync(`virsh qemu-agent-command ${domain} '{"execute":"guest-exec","arguments":{"path":"/bin/sh","arg":["-c","mkdir -p ${HOME} && chown ${user}:${user} ${HOME}"]}}'`, { stdio: "ignore" });
  d.vm(`pkill -f 'vmct[l] web'; rm -rf ${HOME}/qemu-iso-lab ${HOME}/.local ~/lab/demo/qemu-iso-lab ~/.local/share/applications/qemu-iso-lab.desktop; mkdir -p ~/lab/demo; rm -f /tmp/demo-prompt`);
  // The clip's shell: demo.bashrc with the demo home (the real session's menu still gets the entry).
  d.vm(`cat > ${RC} <<'EOF'
# The recorded browser belongs to the real session: its path before HOME changes.
export BROWSER="$HOME/lab/video/open-in-cdp.sh"
export HOME=${HOME}
export PATH="${HOME}/.local/bin:$PATH"
PS1="\\[\\e[1;32m\\]demo@lab\\[\\e[0m\\]:\\[\\e[1;34m\\]\\w\\[\\e[0m\\]\\$ "
PROMPT_COMMAND="date +%s.%N > /tmp/demo-prompt"
cd ${HOME}
clear
EOF`);
  const pages = d.page.context().pages();
  for (const p of pages.slice(1)) await p.close();
  d.page = pages[0];
  await d.page.goto("about:blank");
  await openDemoTerminal(d);
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

// openTerminal() with this clip's rcfile.
async function openDemoTerminal(d) {
  d.vm(`pkill -x qterminal; sleep 1.5; sed -i 's/^fontSize=.*/fontSize=17/' ~/.config/qterminal.org/qterminal.ini; ` +
       `DISPLAY=:0 setsid -f qterminal -e 'bash --rcfile ${RC} -i' </dev/null >/dev/null 2>&1`);
  for (let i = 0; i < 40; i++) {
    await d.sleep(250);
    try { d.vm("DISPLAY=:0 xdotool search --class qterminal >/dev/null"); break; } catch {}
  }
  await d.sleep(800);
  d.vm("DISPLAY=:0 sh -c 'w=$(xdotool search --class qterminal | tail -1); xdotool windowactivate --sync $w; wmctrl -i -r $w -b add,maximized_vert,maximized_horz 2>/dev/null || xdotool windowsize $w 1600 860 windowmove $w 0 0'");
  await d.sleep(800);
}

async function waitFor(d, probe, seconds) {
  for (let i = 0; i < seconds; i++) {
    if (d.vm(`${probe} >/dev/null 2>&1 && echo y || echo n`).trim() === "y") return true;
    await d.sleep(1000);
  }
  return false;
}

export async function run(d) {
  await d.cue("intro");
  await d.sleep(1500);
  await d.cue("line");
  await d.run('sh -c "$(curl -fsSL https://manzolo.github.io/qemu-iso-lab/install.sh)"', { wait: false });
  // The clone, then setup.sh lists what it would install and asks.
  if (!await waitFor(d, "pgrep -f 'vmctl setup --instal[l]'", 120)) throw new Error("setup.sh did not start");
  await d.sleep(3500);
  await d.cue("ask");
  await d.sleep(2500);
  await d.type("y", 200);
  await d.key("Return");
  await d.sleep(6000);
  await d.cue("checked");
  // Setup ends on the welcome screen, which waits for "What now? [1-4]".
  if (!await waitFor(d, "pgrep -f 'vmct[l] welcome'", 300)) throw new Error("no welcome screen");
  // The menu entry and icon went to the demo home: the real session's menu gets a copy (off screen).
  d.vm(`mkdir -p ~/.local/share/applications ~/.local/share/icons/hicolor/scalable/apps; ` +
       `cp ${HOME}/.local/share/applications/qemu-iso-lab.desktop ~/.local/share/applications/; ` +
       `cp ${HOME}/.local/share/icons/hicolor/scalable/apps/qemu-iso-lab.svg ~/.local/share/icons/hicolor/scalable/apps/`);
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
  await d.sleep(2500);
  // The applications menu with the new entry: back to the desktop (the terminal in front, the
  // panel visible), the menu opened from its panel button, "qemu" typed into its search.
  await d.focusTerminal();
  await d.sleep(800);
  await d.cue("menu");
  d.vm("DISPLAY=:0 sh -c 'xdotool mousemove 15 888 click 1'");
  await d.sleep(1500);
  await d.type("qemu", 160);
  await d.sleep(3500);
  await d.key("Escape");
  await d.sleep(800);
  await d.cue("next");
  await d.sleep(1000);
  // Clips 3-6 find the demo where they always did, and the session's vmctl is the demo's.
  d.vm(`ln -sfn ${HOME}/qemu-iso-lab ~/lab/demo/qemu-iso-lab; mkdir -p ~/.local/bin; for b in vmctl vmtui qemu-iso-lab; do ln -sfn ${HOME}/qemu-iso-lab/bin/$b ~/.local/bin/$b; done`);
}
