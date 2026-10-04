// Records one clip on the recording VM's desktop (docs/TOUR.md):
//   TOUR_VM=user@host PLAYWRIGHT_MODULE=.../playwright/index.mjs node tools/tour/rec.mjs tools/tour/clips/01-catalog.mjs [--dry]
// Playwright (over CDP, tunnel 127.0.0.1:19222) finds things, xdotool in the VM moves the real
// pointer and types, ffmpeg x11grab in the VM records. Every cue's start is logged for the subtitles.
import { execFileSync, spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE);
const clipPath = process.argv[2];
const dry = process.argv.includes("--dry");
const clip = await import(pathToFileURL(clipPath).href);
const name = basename(clipPath, ".mjs");
const ROOT = join(dirname(new URL(import.meta.url).pathname), "..", "..");
const outDir = join(ROOT, "artifacts", "tour", "out", name);
mkdirSync(outDir, { recursive: true });

const VM = process.env.TOUR_VM;
if (!VM) throw new Error("TOUR_VM=user@host of the recording VM (docs/TOUR.md)");
const SSH = ["-o", "BatchMode=yes", "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/tour-ssh-%C", "-o", "ControlPersist=15m"];
const vm = (cmd) => execFileSync("ssh", [...SSH, VM, cmd], { encoding: "utf8" });
const vm_ = vm;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Reading time of a cue: the longer language, ~14 characters a second, never under 3.5 s.
const readMs = (c) => Math.max(3500, (Math.max(c.en.length, c.it.length) / 14) * 1000 + 800);

const browser = await chromium.connectOverCDP("http://127.0.0.1:19222");
const ctx = browser.contexts()[0];
let page = ctx.pages()[0] || (await ctx.newPage());

const log = [];
const ff = [];
const steps = [];  // the commands shown, with the moment they are typed: the player's "command at this moment"
let t0 = 0, current = null;

const d = {
  get page() { return page; },
  set page(p) { page = p; },
  vm,
  sleep,
  // A command the viewer may want to copy, typed now (run() records its own; guest sessions call it).
  step(cmd) { steps.push({ start: (Date.now() - t0) / 1000, cmd }); },
  async cue(id) {
    const c = clip.cues.find((x) => x.id === id);
    if (!c) throw new Error(`no cue ${id}`);
    if (current) {
      const held = Date.now() - current.startMs;
      if (held < current.minMs) await sleep(current.minMs - held);
      current.entry.end = (Date.now() - t0) / 1000;
    }
    const entry = { id, start: (Date.now() - t0) / 1000, end: null, en: c.en, it: c.it, top: !!c.top };
    log.push(entry);
    current = { entry, startMs: Date.now(), minMs: readMs(c) };
    console.log(`[${entry.start.toFixed(1)}] ${id}: ${c.en}`);
  },
  async box(locator) {
    await locator.scrollIntoViewIfNeeded();
    const b = await locator.boundingBox();
    if (!b) throw new Error("element not visible");
    const k = await page.evaluate(() => window.devicePixelRatio);
    return { x: (b.x + b.width / 2) * k, y: (b.y + b.height / 2) * k };
  },
  async hover(locator, dur = 0.7) {
    const p = await d.box(locator);
    vm(`DISPLAY=:0 ~/lab/video/mv.py ${p.x} ${p.y} ${dur}`);
  },
  async click(locator, dur = 0.7) {
    const p = await d.box(locator);
    vm(`DISPLAY=:0 ~/lab/video/mv.py ${p.x} ${p.y} ${dur} click`);
    await sleep(400);
  },
  async type(text, delay = 90) {
    const q = text.replace(/'/g, `'\\''`);
    vm(`DISPLAY=:0 xdotool type --delay ${delay} -- '${q}'`);
  },
  // Fast-forward in the edit: from now until ffEnd(), the video runs `factor` times faster.
  ff(factor = 8) { ff.push({ start: (Date.now() - t0) / 1000, end: null, factor }); },
  ffEnd() { const s = ff[ff.length - 1]; if (s && s.end === null) s.end = (Date.now() - t0) / 1000; },
  async key(keys) { vm(`DISPLAY=:0 xdotool key ${keys}`); },
  // A maximized qterminal with the demo shell (prompt demo@lab, BROWSER = the CDP Chromium).
  async openTerminal() {
    vm("pkill -x qterminal; sleep 1.5; sed -i 's/^fontSize=.*/fontSize=17/' ~/.config/qterminal.org/qterminal.ini; " +
       "DISPLAY=:0 setsid -f qterminal -e 'bash --rcfile ~/lab/video/demo.bashrc -i' </dev/null >/dev/null 2>&1");
    for (let i = 0; i < 40; i++) {
      await sleep(250);
      try { vm("DISPLAY=:0 xdotool search --class qterminal >/dev/null"); break; } catch {}
    }
    await sleep(800);
    vm("DISPLAY=:0 sh -c 'w=$(xdotool search --class qterminal | tail -1); xdotool windowactivate --sync $w; wmctrl -i -r $w -b add,maximized_vert,maximized_horz 2>/dev/null || xdotool windowsize $w 1600 860 windowmove $w 0 0'");
    await sleep(800);
  },
  async focusTerminal() { vm("DISPLAY=:0 sh -c 'xdotool windowactivate --sync $(xdotool search --class qterminal | tail -1)'"); await sleep(500); },
  async focusBrowser() { vm("DISPLAY=:0 sh -c 'xdotool windowactivate --sync $(xdotool search --class chromium | tail -1)'"); await sleep(500); },
  // Types a command, presses Enter and waits for the next prompt (or only `ms` when wait is false).
  async run(cmd, { wait = true, timeout = 600000, delay = 70, record = true } = {}) {
    const before = vm("cat /tmp/demo-prompt 2>/dev/null || true");
    if (record && cmd !== "clear") d.step(cmd);
    // A leading space: the first character typed right after a focus change is sometimes lost
    // (xdotool, "lear: command not found" in a take), and bash ignores the space.
    await d.type(" " + cmd, delay);
    await sleep(350);
    await d.key("Return");
    if (!wait) return;
    const until = Date.now() + timeout;
    while (Date.now() < until) {
      await sleep(400);
      if (vm("cat /tmp/demo-prompt 2>/dev/null || true") !== before) return;
    }
    throw new Error(`no prompt after: ${cmd}`);
  },
  // A fresh `vmctl web` of the demo checkout, detached, its URL (with the token) returned.
  restartWeb() {
    try { vm("pkill -f 'vmct[l] web'"); } catch {}
    vm("cd ~/lab/demo/qemu-iso-lab && setsid -f ./bin/vmctl web > /tmp/web.log 2>&1 < /dev/null; " +
       "for i in $(seq 40); do grep -q token= /tmp/web.log && break; sleep 0.25; done");
    return vm("grep -o 'http://127.0.0.1:8765/?token=[A-Za-z0-9_-]*' /tmp/web.log | tail -1").trim();
  },
  // --- Lessons: a session inside a guest, one command at a time, each waiting for the guest's own prompt.
  // session(vm) types `vmctl shell <vm>` on the host terminal, waits until the guest sees a pts (the
  // typed commands must never queue up on the host shell), then installs a PROMPT_COMMAND stamp;
  // guest(cmd) types the command, waits for the stamp to move (read through a second, non-interactive
  // vmctl shell) and leaves the output on screen for `read` ms; leave() exits and waits for the host prompt.
  async session(vm, checkout = "~/lab/demo/qemu-iso-lab") {
    d._guest = { vm, checkout };
    await d.run(`vmctl shell ${vm}`, { wait: false });
    // A login can take a minute (pam_motd on a server): past 4 s the wait is fast-forwarded in the edit.
    const started = Date.now();
    let fast = false;
    let inside = false;
    for (let i = 0; i < 90; i++) {
      await sleep(1000);
      const probe = vm_(`cd ${checkout} && ./bin/vmctl shell ${vm} -- 'who; pgrep -af "sshd:.*@pts"' 2>/dev/null | grep -c pts || true`).trim();
      if (probe !== "0" && probe !== "") { inside = true; break; }
      if (!fast && Date.now() - started > 4000) { d.ff(8); fast = true; }
    }
    if (fast) d.ffEnd();
    // Never go on without the session: the clip's guest commands would be typed into the studio's
    // own terminal (mysql-lab, 2026-10-04: a lab VM that failed its install, `sudo mysql` asking the
    // studio's password).
    if (!inside) throw new Error(`no SSH session in ${vm} after 90 s: is it running? (vmctl status, its post-install log)`);
    await sleep(1500);
    await d.type("PROMPT_COMMAND='date +%s%N >/tmp/.p'; clear", 30);
    await d.key("Return");
    await sleep(1500);
  },
  async guest(cmd, { read = 3500, delay = 40, timeout = 120000 } = {}) {
    const { vm: g, checkout } = d._guest;
    const stamp = () => vm_(`cd ${checkout} && ./bin/vmctl shell ${g} -- cat /tmp/.p 2>/dev/null || true`).trim();
    const before = stamp();
    if (cmd !== "clear") d.step(cmd);
    await d.type(" " + cmd, delay);  // see run(): the first character after a focus change can be lost
    await sleep(300);
    await d.key("Return");
    const until = Date.now() + timeout;
    while (Date.now() < until) {
      await sleep(600);
      if (stamp() !== before) break;
    }
    await sleep(read);
  },
  async leave() {
    d.step("exit");
    await d.run("exit", { record: false });  // the guest's stamp stops with the session: wait for the host's prompt
    d._guest = null;
  },
  // The newest tab of the CDP browser (vmctl web --open makes one).
  async newestPage() {
    for (let i = 0; i < 40; i++) {
      const pages = ctx.pages();
      const last = pages[pages.length - 1];
      if (last && last !== page) { page = last; await page.waitForLoadState(); return page; }
      await sleep(250);
    }
    throw new Error("no new tab");
  },
  // Smooth page scroll by dy pixels over ms.
  async scroll(dy, ms = 1200) {
    await page.evaluate(([dy, ms]) => new Promise((done) => {
      const y0 = window.scrollY, t0 = performance.now();
      const step = (t) => {
        const k = Math.min(1, (t - t0) / ms), e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
        window.scrollTo(0, y0 + dy * e);
        k < 1 ? requestAnimationFrame(step) : done();
      };
      requestAnimationFrame(step);
    }), [dy, ms]);
  },
};

if (clip.setup) await clip.setup(d);
if (!dry) {
  // The screen must be 1600x900 when the capture starts: a viewer opened on the VM (virt-manager)
  // can resize it, and x11grab then refuses the area and records nothing while the clip plays on
  // (mysql lesson, 2026-10-04: seven minutes lost at 1152x768).
  vm("pkill -x spice-vdagent; DISPLAY=:0 xrandr --output Virtual-1 --mode 1600x900; true");
  const size = vm("DISPLAY=:0 xdpyinfo | awk '/dimensions:/{print $2}'").trim();
  if (size !== "1600x900") throw new Error(`the recording VM's screen is ${size}, not 1600x900`);
  vm("rm -f ~/lab/video/rec.mkv; setsid -f ffmpeg -loglevel error -y -f x11grab -framerate 30 -video_size 1600x900 -i :0 -c:v libx264 -preset ultrafast -crf 16 ~/lab/video/rec.mkv </dev/null >/tmp/ff.log 2>&1");
  await sleep(2000);
  // Alive and silent is enough: the file stays empty for a few seconds while x264 fills its buffers.
  if (!vm("pgrep -f 'x11gra[b]' >/dev/null && ! test -s /tmp/ff.log && echo rec || true").includes("rec"))
    throw new Error(`ffmpeg is not recording: ${vm("cat /tmp/ff.log").trim()}`);
}
t0 = Date.now();
try {
  await clip.run(d);
  if (current) {
    const held = Date.now() - current.startMs;
    if (held < current.minMs) await sleep(current.minMs - held);
    current.entry.end = (Date.now() - t0) / 1000;
  }
  await sleep(1200);
} finally {
  if (!dry) {
    vm("pkill -INT -f 'x11gra[b]'; for i in $(seq 50); do pgrep -f 'x11gra[b]' >/dev/null || break; sleep 0.2; done");
    execFileSync("scp", [...SSH, "-q", `${VM}:lab/video/rec.mkv`, join(outDir, "raw.mkv")]);
  }
  writeFileSync(join(outDir, "cues.json"), JSON.stringify({ clip: name, title: clip.title, lab: clip.lab || null, series: clip.series || "tour", cues: log, steps, ff }, null, 2));
  console.log(`-> ${outDir}`);
}
process.exit(0);
