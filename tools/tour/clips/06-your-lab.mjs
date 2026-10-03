export const title = { en: "6 · Your own lab", it: "6 · Il tuo lab" };

const LAB = "my-firewall";
const MEMBERS = ["my-firewall-router", "my-firewall-server", "my-firewall-client"];
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
const NEW = `vmctl group new ${LAB} --title "My firewall lab" --member router=ubuntu-cloud-base --member server=ubuntu-cloud-base --member client=ubuntu-cloud-base`;

export const cues = [
  { id: "idea", en: "Say you want to try firewall rules: a router, a server behind it, a client. Not in the catalog? Make it your own lab.",
    it: "Mettiamo che tu voglia provare delle regole di firewall: un router, un server dietro, un client. Non è nel catalogo? Fattelo, come tuo lab." },
  { id: "dryrun", en: "One command names the lab and its members, each built on a catalog base. First a dry run…",
    it: "Un comando dà il nome al lab e ai suoi membri, ognuno costruito su una base del catalogo. Prima una prova a vuoto…" },
  { id: "estimate", en: "…which shows what it would cost: memory, CPUs, disk, the private network and the SSH ports it picked.",
    it: "…che dice quanto costerebbe: memoria, CPU, disco, la rete privata e le porte SSH che ha scelto." },
  { id: "create", en: "For real now. Nothing in the repository changes: the profiles go in your private local.json…",
    it: "Adesso per davvero. Nel repository non cambia nulla: i profili vanno nel tuo local.json privato…" },
  { id: "files", en: "…and the lab gets its own folder: a guide in two languages, the exercises, and tests that check them.",
    it: "…e il lab ha la sua cartella: una guida in due lingue, gli esercizi e i test che li verificano." },
  { id: "card", en: "In the dashboard it is a lab like the others: its card appears next to the catalog's.",
    it: "Nella dashboard è un lab come gli altri: la sua card compare accanto a quelle del catalogo." },
  { id: "install", en: "Install lab: three cloud images, a couple of minutes each.",
    it: "Install lab: tre cloud image, un paio di minuti l'una." },
  { id: "tests", en: "Run tests: every member reaches the others on the private network, and the Internet through its own NAT.",
    it: "Run tests: ogni membro raggiunge gli altri sulla rete privata, e Internet dal suo NAT." },
  { id: "exercise", en: "Now the laboratory part. Add an exercise to lab.json: a rule on the router that drops ping from the client…",
    it: "Ora la parte da laboratorio. Aggiungi un esercizio a lab.json: una regola sul router che scarta il ping dal client…" },
  { id: "test", en: "…and a test that installs nftables, applies the rule and expects the ping to fail. Run it: the rule works.",
    it: "…e un test che installa nftables, applica la regola e si aspetta che il ping fallisca. Lancialo: la regola funziona." },
  { id: "end", top: true, en: "Add machines, exercises and tests as you go; group remove takes the lab away when you are done.",
    it: "Aggiungi macchine, esercizi e test man mano; group remove toglie il lab quando hai finito." },
];

const EXERCISE = `{
  "title": "Drop ping from the client",
  "text": "A first nftables rule on the router.",
  "blocks": [
    {"kind": "do", "where": "my-firewall-router",
     "commands": ["sudo apt-get install -y nftables", "sudo nft add table inet fw", "sudo nft add chain inet fw input '{ type filter hook input priority 0; }'",
                  "sudo nft add rule inet fw input ip saddr 172.20.3.3 icmp type echo-request drop"]},
    {"kind": "check", "where": "my-firewall-client", "commands": ["ping -c 2 -W 2 172.20.3.1   # must fail"]},
    {"kind": "try", "where": "my-firewall-router", "commands": ["sudo nft flush ruleset   # back to open"]}
  ]
}`;

const TEST = `#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "\${BASH_SOURCE[0]}")/../../../labs/_common.sh"

on my-firewall-router sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nftables
on my-firewall-router sudo nft add table inet fw
on my-firewall-router sudo nft add chain inet fw input "{ type filter hook input priority 0; }"
on my-firewall-router sudo nft add rule inet fw input ip saddr 172.20.3.3 icmp type echo-request drop
assert_fail "the router drops ping from the client" on my-firewall-client ping -c 2 -W 2 172.20.3.1
assert "the server still reaches the router" on my-firewall-server ping -c 2 172.20.3.1
on my-firewall-router sudo nft flush ruleset
report_results "my-firewall rules"
`;

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && for v in ${MEMBERS.join(" ")}; do ./bin/vmctl stop $v >/dev/null 2>&1; done; ` +
       `./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; ./bin/vmctl group remove ${LAB} >/dev/null 2>&1; rm -rf artifacts/${LAB}-*; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

async function waitJob(d, pattern, extra = "true") {
  for (let i = 0; i < 1800; i++) {
    const s = d.vm(`if pgrep -f '${pattern}' >/dev/null; then echo busy; elif ${extra}; then echo done; else echo busy; fi`).trim();
    if (s === "done") return;
    await d.sleep(1000);
  }
  throw new Error(`job still running: ${pattern}`);
}

export async function run(d) {
  await d.cue("idea");
  await d.run("cd qemu-iso-lab");
  await d.sleep(1500);
  await d.cue("dryrun");
  await d.run(`${NEW} --dry-run`, { delay: 45 });
  await d.sleep(1200);
  await d.cue("estimate");
  await d.sleep(3500);
  await d.cue("create");
  await d.run(NEW, { delay: 45 });
  await d.sleep(1500);
  await d.cue("files");
  await d.run(`find vms/labs.local/${LAB} -type f | sort`);
  await d.sleep(2500);

  await d.cue("card");
  const url = d.restartWeb();
  d.vm(`DISPLAY=:0 ~/lab/video/open-in-cdp.sh '${url}'`);
  const p = await d.newestPage();
  await d.focusBrowser();
  await p.locator("#search").waitFor();
  await d.click(p.getByRole("button", { name: "Labs", exact: true }).first());
  const card = () => p.locator(`section.lab[data-lab="${LAB}"]`);
  await card().scrollIntoViewIfNeeded();
  await d.hover(card().locator(".lab-name"));
  await d.sleep(2000);

  await d.cue("install");
  await d.click(card().locator(".lab-footer .buttons button").first());
  await d.sleep(2000);
  await d.click(p.locator("#job-bar-log"));
  await d.sleep(2500);
  d.ff(16);
  await waitJob(d, "vmct[l] group install", `grep -q verify ${CHECKOUT}/artifacts/${MEMBERS[2]}/state.json`);
  await d.sleep(1500);
  d.ffEnd();
  await d.click(p.locator("#log-dialog [data-close]"));
  await d.sleep(1000);

  await d.cue("tests");
  await card().scrollIntoViewIfNeeded();
  await d.click(card().getByRole("button", { name: "Run tests" }));
  await d.sleep(1500);
  await d.click(p.locator("#job-bar-log"));
  await d.sleep(2000);
  d.ff(4);
  await waitJob(d, "vmct[l] group test");
  await d.sleep(800);
  d.ffEnd();
  await d.sleep(3000);
  await d.click(p.locator("#log-dialog [data-close]"));

  await d.cue("exercise");
  // The exercise goes into lab.json and the test file is written: typed slowly enough to read.
  d.vm(`cd ${CHECKOUT} && python3 - <<'PY'
import json, pathlib
p = pathlib.Path("vms/labs.local/${LAB}/lab.json"); d = json.loads(p.read_text())
d["exercises"].append(json.loads('''${EXERCISE}'''))
p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\\n")
PY
cat > vms/labs.local/${LAB}/tests/test_03_rules.sh <<'SH'
${TEST}SH`);
  await d.focusTerminal();
  await d.run("clear");
  await d.run(`python3 -c "import json; print(json.dumps(json.load(open('vms/labs.local/${LAB}/lab.json'))['exercises'][-1], indent=2))"`, { delay: 30 });
  await d.sleep(4000);
  await d.cue("test");
  await d.run(`cat vms/labs.local/${LAB}/tests/test_03_rules.sh`, { delay: 40 });
  await d.sleep(3500);
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group test ${LAB}`, { wait: false });
  await d.sleep(6000);
  d.ff(6);
  for (let i = 0; i < 600 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(4000);
  await d.cue("end");
  await d.sleep(1500);
}
