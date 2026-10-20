export const title = { en: "One login for every app: the Keycloak lab", it: "Un login per tutte le app: il lab Keycloak" };
export const series = "labs";
export const lab = "keycloak-lab";

const LAB = "keycloak-lab";
const SERVER = "keycloak-lab-server", CLIENT = "keycloak-lab-client";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
const KC = "http://127.0.0.1:18080";    // Keycloak, forwarded to the studio's loopback
const APP = "http://127.0.0.1:18081";   // the OIDC app on the client, same forward

// Rewritten 2026-10-20 evening after Manzolo's verdict on the first take: long silences (one sentence
// per section, then eight commands typed with nobody speaking), a robotic explanation, no thread for
// someone who has never seen this. Now: the thread of a beginner (the problem, the idea, then the
// login SEEN as Alice and Bob, and only then behind the scenes), a sentence for every command or
// page that appears, half the commands, and the install covered by the voice at 30x.
export const cues = [
  { id: "problem", en: "Every app you use asks who you are. If each one keeps its own passwords, you have ten passwords, and whoever runs them has ten places to add or remove a person.",
    it: "Ogni app che usi ti chiede chi sei. Se ognuna si tiene le sue password, tu hai dieci password, e chi le gestisce ha dieci posti dove aggiungere o togliere una persona." },
  { id: "idea", en: "Keycloak is one place that knows who you are. The apps never see your password: they send you to Keycloak and trust its answer. The lab has two machines: a server with Keycloak, and a client with a small web app.",
    it: "Keycloak è un posto solo che sa chi sei. Le app non vedono mai la tua password: ti mandano da lui e si fidano della risposta. Il lab ha due macchine: un server con Keycloak, e un client con una piccola app web." },
  { id: "install", en: "One command installs both, from cloud images. I will go fast here.",
    it: "Un comando le installa tutte e due, da immagini cloud. Qui vado veloce." },
  { id: "app", en: "This is the app. It has no login form of its own: just a Login button.",
    it: "Questa è l'app. Non ha un modulo di login suo: solo un pulsante, Login." },
  { id: "kclogin", en: "The button brings you here, to Keycloak. The password goes to Keycloak, never to the app. I sign in as Alice.",
    it: "Il pulsante ti porta qui, da Keycloak. La password la dai a Keycloak, mai all'app. Entro come Alice." },
  { id: "back", en: "Alice is in, and the app knows it without ever seeing her password. The protected page opens.",
    it: "Alice è dentro, e l'app lo sa senza aver mai visto la sua password. La pagina protetta si apre." },
  { id: "editok", en: "The editor page is for people with the role editor. Alice has it: it opens.",
    it: "La pagina dell'editor è per chi ha il ruolo editor. Alice ce l'ha: si apre." },
  { id: "bob", en: "I log out, and sign in as Bob.",
    it: "Esco, ed entro come Bob." },
  { id: "edit403", en: "Bob signed in just like Alice, but he has no editor role: 403, forbidden. This is the difference between authentication, who you are, and authorization, what you may do.",
    it: "Bob è entrato come Alice, ma non ha il ruolo editor: 403, vietato. È la differenza tra autenticazione, chi sei, e autorizzazione, cosa puoi fare." },
  { id: "console", en: "Behind all this is the server. Its admin console is where Alice, Bob and the role editor were created.",
    it: "Dietro c'è il server. La sua console di amministrazione è dove Alice, Bob e il ruolo editor sono stati creati." },
  { id: "users", en: "A realm is a world of its own, with its users and its apps. Ours is called lab: two users.",
    it: "Un realm è un mondo a sé, con i suoi utenti e le sue app. Il nostro si chiama lab: due utenti." },
  { id: "clients", en: "And the registered apps. lab-web is ours, with the address Keycloak may send the browser back to. An app that is not in this list cannot ask for a login.",
    it: "E le app registrate. lab-web è la nostra, con l'indirizzo a cui Keycloak può rimandare il browser. Un'app che non è in questa lista non può chiedere un login." },
  { id: "cli", en: "The same things can be done from the command line, with kcadm. Here I give Bob the role editor.",
    it: "Le stesse cose si fanno da riga di comando, con kcadm. Qui do a Bob il ruolo editor." },
  { id: "cliback", en: "Now Bob has it. And I take it back at once: in the lab, Bob stays without.",
    it: "Ora Bob ce l'ha. E glielo tolgo subito: nel lab, Bob resta senza." },
  { id: "token", en: "What did the app receive instead of a password? A token: a signed ticket from Keycloak. Inside: who issued it, for whom, until when, and the roles. The app checks the signature and trusts it.",
    it: "Che cosa ha ricevuto l'app, al posto della password? Un token: un biglietto firmato da Keycloak. Dentro: chi l'ha emesso, per chi, fino a quando, e i ruoli. L'app controlla la firma e si fida." },
  { id: "discovery", en: "How does the app know the keys that check the signature? It asks Keycloak, at a public address: the discovery.",
    it: "Come fa l'app a conoscere le chiavi per controllare la firma? Le chiede a Keycloak, a un indirizzo pubblico: la discovery." },
  { id: "service", en: "Not only people: a program can identify itself too, with a secret of its own. This token lives five seconds, and right now it is active.",
    it: "Non solo persone: anche un programma può farsi riconoscere, con un segreto suo. Questo token vive cinque secondi, e in questo momento è attivo." },
  { id: "expired", en: "Six seconds later it is worth nothing. A stolen token is worth little too.",
    it: "Sei secondi dopo non vale più niente. Anche un token rubato vale poco." },
  { id: "wrong", en: "And with a wrong secret, Keycloak says no: 401.",
    it: "E con un segreto sbagliato, Keycloak dice di no: 401." },
  { id: "tests", en: "The lab has seven tests that do all of this on their own, the login included, and tell you if it still works.",
    it: "Il lab ha sette test che rifanno tutto questo da soli, login compreso, e ti dicono se funziona ancora." },
  { id: "end", top: true, en: "Commands and explanations are in the guide, in English and Italian, from the lab's card.",
    it: "Comandi e spiegazioni sono nella guida, in italiano e in inglese, dalla card del lab." },
];

export async function setup(d) {
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl stop ${CLIENT} >/dev/null 2>&1; ./bin/vmctl stop ${SERVER} >/dev/null 2>&1; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await ctx.clearCookies();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

const say = (d, cmd, read = 3000) => d.guest(cmd, { read });
const fast = (d, cmd, read = 3000) => d.guest(cmd, { read, delay: 12 });

// Keycloak's login form (realm lab): username, password, Sign in; back to the app when done.
async function keycloakLogin(d, page, user, password) {
  await page.locator("#username").waitFor({ timeout: 60000 });
  await d.sleep(800);
  await d.click(page.locator("#username"));
  await d.type(user);
  await d.click(page.locator("#password"));
  await d.type(password);
  await d.click(page.locator("#kc-login"));
  await page.waitForURL(/127\.0\.0\.1:18081/, { timeout: 60000 });
  await page.waitForLoadState("networkidle");
}
const open = async (d, page, url, ms) => { await page.goto(url); await page.waitForLoadState("networkidle"); await d.sleep(ms); };

export async function run(d) {
  const page = d.page;
  // The problem and the idea, over the empty terminal
  await d.cue("problem");
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.sleep(10000);
  await d.cue("idea");
  await d.sleep(13000);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(2500);
  d.ff(30);
  for (let i = 0; i < 1500 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  // Keycloak (Java) comes up well after SSH does: wait for it off camera before anyone uses it
  d.offCamera(SERVER, "for i in $(seq 1 150); do curl -fsS http://127.0.0.1:8080/realms/lab/.well-known/openid-configuration >/dev/null 2>&1 && break; sleep 2; done");

  // Seen as a user: Alice, then Bob
  await d.cue("app");
  await d.focusBrowser();
  await page.bringToFront();
  await open(d, page, `${APP}/`, 2500);
  await d.click(page.locator("a[href='/login']"));
  await d.cue("kclogin");
  await keycloakLogin(d, page, "alice", "alice-lab-pass");
  await d.cue("back");
  await d.sleep(1500);
  await open(d, page, `${APP}/protected`, 4000);
  await d.cue("editok");
  await open(d, page, `${APP}/edit`, 4000);
  await d.cue("bob");
  await open(d, page, `${APP}/actions`, 800);
  await d.click(page.locator("form[action='/logout'] button"));
  await page.waitForURL(/127\.0\.0\.1:18081\/?$/, { timeout: 60000 });
  await page.waitForLoadState("networkidle");
  await d.sleep(800);
  await d.click(page.locator("a[href='/login']"));
  await keycloakLogin(d, page, "bob", "bob-lab-pass");
  await d.cue("edit403");
  await open(d, page, `${APP}/edit`, 9000);

  // Behind the scenes: the console, then the command line
  await d.cue("console");
  await open(d, page, `${KC}/admin/master/console/`, 500);
  await keycloakLogin(d, page, "admin", "admin-lab-pass").catch(() => {});
  await page.waitForLoadState("networkidle");
  await d.sleep(2500);
  await d.cue("users");
  // a realm's view needs a fresh load: changing only the hash kept the master realm on screen
  await page.goto("about:blank");
  await open(d, page, `${KC}/admin/master/console/#/lab/users`, 6000);
  await d.cue("clients");
  await page.goto("about:blank");
  await open(d, page, `${KC}/admin/master/console/#/lab/clients`, 8000);
  await page.goto("about:blank");
  await d.focusTerminal();

  await d.cue("cli");
  await d.clearScreen();
  await d.session(SERVER);
  await say(d, "kc-lab add-roles -r lab --uusername bob --rolename editor", 2500);
  await d.cue("cliback");
  await say(d, "kc-lab get-roles -r lab --uusername bob | grep name", 3000);
  await say(d, "kc-lab remove-roles -r lab --uusername bob --rolename editor", 2500);
  await d.leave();

  // What the app received, and how it checks it
  await d.cue("token");
  await d.focusBrowser();
  await page.bringToFront();
  await open(d, page, `${APP}/tokens`, 3500);
  await d.scroll(540, 1500);  // from the raw token down to the checked claims
  await d.sleep(6500);
  await page.goto("about:blank");
  await d.focusTerminal();
  await d.cue("discovery");
  await d.clearScreen();
  await d.session(CLIENT);
  await say(d, "curl -fsS http://172.20.20.1:8080/realms/lab/.well-known/openid-configuration | jq '{issuer, jwks_uri}'", 5000);

  await d.cue("service");
  await d.clearScreen();
  await say(d, "P=http://172.20.20.1:8080/realms/lab/protocol/openid-connect", 800);
  await say(d, "TOKEN=$(curl -fsS \"$P/token\" -d grant_type=client_credentials -d client_id=lab-service -d client_secret=service-lab-secret | jq -r .access_token)", 800);
  await fast(d, "curl -fsS \"$P/token/introspect\" -d client_id=lab-web -d client_secret=web-lab-secret --data-urlencode \"token=$TOKEN\" | jq '{active, client_id, exp}'", 4000);
  await d.cue("expired");
  await fast(d, "sleep 6; curl -fsS \"$P/token/introspect\" -d client_id=lab-web -d client_secret=web-lab-secret --data-urlencode \"token=$TOKEN\" | jq", 4500);
  await d.cue("wrong");
  await fast(d, "curl -si \"$P/token\" -d grant_type=client_credentials -d client_id=lab-service -d client_secret=wrong | head -1", 4000);
  await d.leave();

  await d.cue("tests");
  await d.clearScreen();
  const before2 = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group test ${LAB}`, { wait: false });
  await d.sleep(4000);
  d.ff(8);
  for (let i = 0; i < 900 && d.vm("cat /tmp/demo-prompt") === before2; i++) await d.sleep(1000);
  d.ffEnd();
  await d.sleep(3000);
  await d.cue("end");
  await d.sleep(2000);
}
