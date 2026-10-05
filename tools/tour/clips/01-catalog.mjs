export const title = { en: "1 · The catalog: pick a machine", it: "1 · Il catalogo: scegliere una macchina" };

export const cues = [
  { id: "intro", en: "qemu-iso-lab is a catalog of virtual machines that install themselves, on plain QEMU.",
    it: "qemu-iso-lab è un catalogo di macchine virtuali che si installano da sole, su QEMU puro." },
  { id: "counts", en: "Almost two hundred profiles: most install unattended, and most were verified live.",
    it: "Quasi duecento profili: la maggior parte si installa senza domande, e quasi tutti sono stati verificati dal vivo." },
  { id: "search", en: "Search by name or version…",
    it: "Si cerca per nome o per versione…" },
  { id: "card", en: "Every card tells how the machine installs, its version, when it last passed, and what it needs.",
    it: "Ogni scheda dice come si installa la macchina, la sua versione, quando ha passato l'ultima prova e cosa le serve." },
  { id: "clip", en: "The clip is the real install, recorded during a test run.",
    it: "La clip è l'installazione vera, registrata durante una prova." },
  { id: "commands", en: "And these are the two commands that install and start it.",
    it: "E questi sono i due comandi che la installano e la avviano." },
  { id: "filters", en: "Filters narrow the list: desktops, servers, a family of systems…",
    it: "I filtri restringono la lista: desktop, server, una famiglia di sistemi…" },
  { id: "pick", en: "Pick a few machines with the plus button…",
    it: "Si scelgono alcune macchine con il pulsante più…" },
  { id: "basket", top: true, en: "…and the catalog writes the command that adds them to your lab.",
    it: "…e il catalogo scrive il comando che le aggiunge al proprio lab." },
  { id: "next", en: "Next: installing qemu-iso-lab on your computer.",
    it: "Prossimo passo: installare qemu-iso-lab sul proprio computer." },
];

export async function setup(d) {
  // Recorded after another clip, the dashboard tab that one left open would stay in front
  // (2026-10-05: a take filmed the local dashboard while the site loaded behind it).
  for (const p of d.page.context().pages()) if (p !== d.page) await p.close();
  await d.page.bringToFront();
  await d.focusBrowser();
  await d.page.goto("https://manzolo.github.io/qemu-iso-lab/");
  await d.page.evaluate(() => { localStorage.clear(); window.scrollTo(0, 0); });
  await d.page.reload();
  await d.page.waitForLoadState("networkidle");
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1500 820 0.1");
}

export async function run(d) {
  const p = d.page;
  await d.cue("intro");
  await d.sleep(1500);
  await d.cue("counts");
  await d.hover(p.locator("#m-profiles"));
  await d.sleep(900);
  await d.hover(p.locator("#m-unattended"));
  await d.sleep(900);
  await d.hover(p.locator("#m-verified"));

  await d.cue("search");
  await d.click(p.locator("#search"));
  await d.type("ubuntu 24.04", 140);
  await d.sleep(1500);

  await d.cue("card");
  const card = p.locator('article.card[data-name="ubuntu-gnome-24.04"]');
  await card.scrollIntoViewIfNeeded();
  await d.hover(card.locator(".facts, .badge").first());
  await d.sleep(1200);
  await d.cue("clip");
  await d.hover(card.locator(".clip img").first());
  await d.sleep(2500);
  await d.cue("commands");
  await d.hover(card.locator(".terminal-step, pre, code").first());
  await d.sleep(1500);

  await d.cue("filters");
  await d.click(p.locator("#search"));
  await d.key("ctrl+a BackSpace");
  await d.scroll(-2000, 800);
  await d.click(p.getByRole("button", { name: "Desktop", exact: true }).first());
  await d.sleep(1200);
  await p.locator("#family").scrollIntoViewIfNeeded();
  await d.hover(p.locator("#family"));
  await p.locator("#family").selectOption({ index: 2 });
  await d.sleep(1800);

  await d.cue("pick");
  const picks = p.locator("article.card .pick");
  await d.click(picks.nth(0));
  await d.sleep(600);
  await d.click(picks.nth(1));
  await d.sleep(800);
  await d.cue("basket");
  await d.hover(p.locator("#basket-cmd"));
  await d.sleep(2500);
  await d.cue("next");
}
