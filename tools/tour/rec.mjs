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
    await d.type(cmd, delay);
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
  vm("rm -f ~/lab/video/rec.mkv; setsid -f ffmpeg -loglevel error -y -f x11grab -framerate 30 -video_size 1600x900 -i :0 -c:v libx264 -preset ultrafast -crf 16 ~/lab/video/rec.mkv </dev/null >/tmp/ff.log 2>&1");
  await sleep(800);
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
