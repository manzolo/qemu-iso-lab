export const title = { en: "Hardening SSH: the SSH lab", it: "Blindare SSH: il lab SSH" };
export const series = "labs";
export const lab = "ssh-lab";

const LAB = "ssh-lab";
const SERVER = "ssh-lab-server", CLIENT = "ssh-lab-client";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
const USER = "demo";  // the guest user chosen in the demo's Welcome form

export const cues = [
  { id: "intro", en: "SSH is how you reach every server you own, so it is the door everyone else tries first. The SSH lab is a server to harden and a client to probe it from, on a private segment.",
    it: "SSH è il modo in cui raggiungi ogni tuo server, quindi è la porta che tutti gli altri provano per prima. Il lab SSH è un server da blindare e un client da cui metterlo alla prova, su un segmento privato." },
  { id: "install", en: "Two cloud images: the server at 172.20.2.1, the client at 172.20.2.2, with fail2ban, knockd and nmap already installed.",
    it: "Due cloud image: il server a 172.20.2.1, il client a 172.20.2.2, con fail2ban, knockd e nmap già installati." },
  { id: "anatomy", en: "On the server: the sshd service, and the three settings that matter most in sshd_config: public keys, passwords, root login.",
    it: "Sul server: il servizio sshd, e le tre impostazioni che contano di più in sshd_config: chiavi pubbliche, password, login di root." },
  { id: "keys", en: "On the client, keys. ssh-keygen makes a pair: ed25519 is short and fast, a 4096-bit RSA key the heavier classic. The fingerprint line names the type.",
    it: "Sul client, le chiavi. ssh-keygen crea una coppia: ed25519 è corta e veloce, una RSA a 4096 bit è il classico più pesante. La riga del fingerprint ne dice il tipo." },
  { id: "knock", en: "Try the server's port 22 from the segment: closed. This server hides SSH behind port knocking: a sequence of three ports opens it for your address only.",
    it: "Prova la porta 22 del server dal segmento: chiusa. Questo server nasconde SSH dietro il port knocking: una sequenza di tre porte la apre solo per il tuo indirizzo." },
  { id: "knocked", en: "knock 7000, 8000, 9000: now the port answers. The reverse sequence closes it again.",
    it: "knock 7000, 8000, 9000: ora la porta risponde. La sequenza al contrario la richiude." },
  { id: "harden", en: "Hardening: no root login, three authentication tries. Always sshd -t before a reload, so a typo never leaves you locked out; then reload, check, and put the file back.",
    it: "Blindatura: niente login di root, tre tentativi di autenticazione. Sempre sshd -t prima di un reload, così un refuso non ti chiude mai fuori; poi reload, verifica, e il file torna com'era." },
  { id: "fail2ban", en: "fail2ban watches the auth log and bans an address after a few failures. The sshd jail is active, with nobody banned yet.",
    it: "fail2ban guarda il log di autenticazione e bandisce un indirizzo dopo qualche fallimento. La jail sshd è attiva, ancora nessun bandito." },
  { id: "noisy", en: "From the client, four wrong passwords in a row…",
    it: "Dal client, quattro password sbagliate di fila…" },
  { id: "banned", en: "…and the server's jail lists the client's address. Unban it, or the next exercise cannot connect.",
    it: "…e la jail del server elenca l'indirizzo del client. Toglilo dal bando, o il prossimo esercizio non si collega." },
  { id: "scan", en: "What a scan sees: nmap finds the port, and with version detection reads the SSH banner. Then close the knock and remove the keys.",
    it: "Cosa vede una scansione: nmap trova la porta, e con il rilevamento della versione legge il banner SSH. Poi chiudi il knock e rimuovi le chiavi." },
  { id: "journal", en: "Every attempt left a trace in the server's journal: this is what you read when you wonder who knocked.",
    it: "Ogni tentativo ha lasciato traccia nel journal del server: è quello che leggi quando ti chiedi chi ha bussato." },
  { id: "tests", en: "The lab's six tests do all of this again, in order, and tell you if the server still defends itself.",
    it: "I sei test del lab rifanno tutto questo, in ordine, e ti dicono se il server si difende ancora." },
  { id: "end", top: true, en: "Every command is in the guide, in English and Italian, from the lab's card.",
    it: "Ogni comando è nella guida, in inglese e in italiano, dalla card del lab." },
];

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl stop ${CLIENT} >/dev/null 2>&1; ./bin/vmctl stop ${SERVER} >/dev/null 2>&1; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

export async function run(d) {
  await d.cue("intro");
  await d.run("cd qemu-iso-lab");
  await d.sleep(3000);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(5000);
  d.ff(12);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(1500);

  await d.cue("anatomy");
  await d.run("clear");
  await d.session(SERVER);
  await d.guest("systemctl is-active ssh", { read: 2000 });
  await d.guest("grep -E '^#?(PubkeyAuthentication|PasswordAuthentication|PermitRootLogin)' /etc/ssh/sshd_config", { read: 4500 });
  await d.leave();

  await d.cue("keys");
  await d.session(CLIENT);
  await d.guest("ssh-keygen -t ed25519 -f ~/.ssh/lab_key -N '' -C lab", { read: 3000 });
  await d.guest("ssh-keygen -l -f ~/.ssh/lab_key.pub", { read: 3000 });
  await d.guest("ssh-keygen -t rsa -b 4096 -f ~/.ssh/lab_rsa -N '' -C lab -q && ssh-keygen -l -f ~/.ssh/lab_rsa.pub", { read: 3500 });
  await d.cue("knock");
  await d.guest("timeout 3 bash -c 'echo > /dev/tcp/172.20.2.1/22' || echo 'port 22: closed from the segment'", { read: 4000 });
  await d.cue("knocked");
  await d.guest("knock 172.20.2.1 7000 8000 9000 && sleep 2", { read: 1500 });
  await d.guest("timeout 3 bash -c 'echo > /dev/tcp/172.20.2.1/22' && echo 'port 22: open for 172.20.2.2'", { read: 4000 });
  await d.leave();

  await d.cue("harden");
  await d.session(SERVER);
  await d.guest("sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak", { read: 1200 });
  await d.guest("sudo sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin no/; s/^#\\?MaxAuthTries.*/MaxAuthTries 3/' /etc/ssh/sshd_config", { read: 1500 });
  await d.guest("sudo sshd -t && echo 'config valid' && sudo systemctl reload ssh", { read: 2500 });
  await d.guest("grep -E '^(PermitRootLogin|MaxAuthTries)' /etc/ssh/sshd_config", { read: 3500 });
  await d.guest("sudo cp /etc/ssh/sshd_config.bak /etc/ssh/sshd_config && sudo systemctl reload ssh    # back as it was", { read: 2500 });
  await d.cue("fail2ban");
  await d.guest("sudo fail2ban-client status sshd", { read: 5000 });
  await d.leave();

  await d.cue("noisy");
  await d.session(CLIENT);
  // ConnectTimeout: once fail2ban bans the client mid-loop, the remaining attempts would hang on a DROP.
  await d.guest(`for i in 1 2 3 4; do sshpass -p nope ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o PreferredAuthentications=password -o PubkeyAuthentication=no ${USER}@172.20.2.1 true 2>/dev/null; echo "attempt $i refused"; done`, { read: 2500, timeout: 90000 });
  await d.leave();

  await d.cue("banned");
  await d.session(SERVER);
  await d.guest("sleep 3; sudo fail2ban-client status sshd | grep -A2 'Banned IP'", { read: 5500 });
  await d.guest("sudo fail2ban-client set sshd unbanip 172.20.2.2", { read: 2500 });
  await d.leave();

  await d.cue("scan");
  await d.session(CLIENT);
  await d.guest("nmap -p 22 172.20.2.1", { read: 4000, timeout: 60000 });
  await d.guest("nmap -sV -p 22 172.20.2.1 | grep -E 'open|Service'", { read: 4500, timeout: 90000 });
  await d.guest("knock 172.20.2.1 9000 8000 7000; rm -f ~/.ssh/lab_key ~/.ssh/lab_key.pub ~/.ssh/lab_rsa ~/.ssh/lab_rsa.pub", { read: 2000 });
  await d.leave();

  await d.cue("journal");
  await d.session(SERVER);
  await d.guest("sudo journalctl -u ssh --no-pager -n 8 -o short", { read: 6000 });
  await d.leave();

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
