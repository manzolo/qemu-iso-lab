export const title = { en: "Who you are and what you may do: the Keycloak lab", it: "Chi sei e cosa puoi fare: il lab Keycloak" };
export const series = "labs";
export const lab = "keycloak-lab";

const LAB = "keycloak-lab";
const SERVER = "keycloak-lab-server", CLIENT = "keycloak-lab-client";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
const KC = "http://127.0.0.1:18080";    // Keycloak, forwarded to the studio's loopback
const APP = "http://127.0.0.1:18081";   // the OIDC app on the client, same forward

export const cues = [
  { id: "intro", en: "Who you are and what you may do are two different questions. Keycloak answers the first for every app you own: one login, one place for users and roles. The lab is a server with Keycloak and a client with a small web app that trusts it.",
    it: "Chi sei e cosa puoi fare sono due domande diverse. Keycloak risponde alla prima per tutte le tue app: un login, un posto per utenti e ruoli. Nel lab: un server con Keycloak e un client con una piccola app web." },
  { id: "install", en: "Two cloud images: the server at 172.20.20.1 with Keycloak 26.4.7 and PostgreSQL, the client at 172.20.20.2 with the app, curl and jq. The install downloads the official archive and checks its hash.",
    it: "Due cloud image: il server con Keycloak e PostgreSQL, il client con l'app, curl e jq. L'installazione scarica l'archivio ufficiale e ne controlla l'impronta." },
  { id: "anatomy", en: "On the server: a systemd service, Keycloak's own version, PostgreSQL behind it instead of the development store, and port 8080. kc-lab wraps kcadm, the command-line admin, with a fresh login every time.",
    it: "Sul server: un servizio systemd, la versione di Keycloak, PostgreSQL dietro, la porta 8080. kc-lab avvolge kcadm, l'amministrazione da riga di comando." },
  { id: "console", en: "The same server in the browser: the admin console, forwarded to port 18080. The realm lab is the lab's fixture: two users, Alice and Bob, two clients, and the role editor.",
    it: "Lo stesso server nel browser: la console di amministrazione, inoltrata sulla porta 18080. Il realm lab è la dotazione del lab: due utenti, Alice e Bob, due client, e il ruolo editor." },
  { id: "kcadm", en: "What the console does with clicks, kcadm does with commands: a practice realm, a public client, a user with a password, a role, the role on the user. Then the practice realm goes away.",
    it: "Quello che la console fa coi clic, kcadm lo fa coi comandi: un realm di prova, un client pubblico, un utente con la password, un ruolo, il ruolo sull'utente. Poi il realm di prova se ne va." },
  { id: "discovery", en: "From the client: discovery. One address tells an app every endpoint of the realm, and the JWKS publishes the public keys that sign the tokens. That is how the app verifies a token without ever asking the server.",
    it: "Dal client: la discovery. Un solo indirizzo dice a un'app ogni endpoint del realm, e il JWKS pubblica le chiavi che firmano i token: così l'app verifica un token senza chiedere al server." },
  { id: "flow", en: "The app, on port 18081. Login sends the browser to Keycloak: the password is typed there, never in the app. Alice comes back with a code; the app trades it for the tokens with its secret and PKCE verifier, and keeps them.",
    it: "Login manda il browser a Keycloak: la password si scrive lì, mai nell'app. Alice torna con un codice; l'app lo scambia con i token, col suo segreto e il verifier PKCE, e li tiene per sé." },
  { id: "claims", en: "The claims the app verified: who issued the token, for whom, until when, and the roles. Nothing here was trusted before the signature checked out.",
    it: "I claim che l'app ha verificato: chi ha emesso il token, per chi, fino a quando, e i ruoli. Niente di questo è stato creduto prima che la firma tornasse." },
  { id: "roles", en: "Authorization: Alice has editor, so the editor page opens. Log out everywhere, log in as Bob: Bob is authenticated and gets 403. Same login, different permission.",
    it: "Autorizzazione: Alice ha editor, quindi la pagina dell'editor si apre. Logout ovunque, login come Bob: Bob è autenticato e prende 403. Stesso login, permesso diverso." },
  { id: "service", en: "A service has no user: client credentials, a token for lab-service that lives five seconds. Introspection says active, the app's API accepts a fresh one… and six seconds later, introspection says inactive.",
    it: "Un servizio non ha un utente: client credentials, un token per lab-service che vive cinque secondi. L'introspection dice attivo, l'API ne accetta uno fresco… e sei secondi dopo, non attivo." },
  { id: "errors", en: "Errors are the part worth reading: a wrong secret is 401 unauthorized client, a redirect address the client never declared is refused before any login, an expired token is 401 at the API.",
    it: "Gli errori vanno letti: segreto sbagliato, 401 unauthorized client; un indirizzo di ritorno mai dichiarato è rifiutato prima del login; un token scaduto è 401 all'API." },
  { id: "journal", en: "And the server's journal keeps every one of those attempts, with the client and the reason.",
    it: "E il journal del server tiene ognuno di quei tentativi, col client e il motivo." },
  { id: "tests", en: "Seven tests, one per exercise, drive the real login form and the real tokens, and tell you if the lab still behaves.",
    it: "Sette test, uno per esercizio, guidano il vero modulo di login e i veri token, e ti dicono se il lab si comporta ancora bene." },
  { id: "end", top: true, en: "Every command is in the guide, in English and Italian, from the lab's card.",
    it: "Ogni comando è nella guida, in inglese e in italiano, dalla card del lab." },
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

// In the approved style (docs/TOUR.md, "How a lesson types its commands"): the lab's own commands,
// one per line, the cleanups off camera.
const say = (d, cmd, read = 3500) => d.guest(cmd, { read });

// Keycloak's login form (realm lab): username, password, Sign in; back to the app when done.
async function keycloakLogin(d, page, user, password) {
  await page.locator("#username").waitFor({ timeout: 60000 });
  await d.sleep(1200);
  await d.click(page.locator("#username"));
  await d.type(user);
  await d.click(page.locator("#password"));
  await d.type(password);
  await d.click(page.locator("#kc-login"));
  await page.waitForURL(/127\.0\.0\.1:18081/, { timeout: 60000 });
  await page.waitForLoadState("networkidle");
}

export async function run(d) {
  const page = d.page;
  await d.cue("intro");
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.sleep(4000);
  await d.cue("install");
  const before = d.vm("cat /tmp/demo-prompt");
  await d.run(`vmctl group install ${LAB} --yes`, { wait: false });
  await d.sleep(5000);
  d.ff(12);
  for (let i = 0; i < 1500 && d.vm("cat /tmp/demo-prompt") === before; i++) await d.sleep(1000);
  d.ffEnd();
  // Keycloak (Java) comes up well after SSH does, more so under the studio's nested KVM: the anatomy
  // commands must find it listening, so the wait stays off camera.
  d.offCamera(SERVER, "for i in $(seq 1 150); do curl -fsS http://127.0.0.1:8080/realms/lab/.well-known/openid-configuration >/dev/null 2>&1 && break; sleep 2; done");
  await d.sleep(1500);

  await d.cue("anatomy");
  await d.clearScreen();
  await d.session(SERVER);
  await say(d, "systemctl is-active keycloak postgresql", 2500);
  await say(d, "/opt/keycloak/bin/kc.sh --version", 4000);
  await say(d, "sudo -u postgres psql -d keycloak -c 'SELECT name FROM realm;'", 3500);
  await say(d, "ss -ltn | grep -E ':(8080|5432) '", 3000);
  await say(d, "kc-lab get realms/lab --fields realm,enabled", 4500);
  await d.leave();

  await d.cue("console");
  await d.focusBrowser();
  await page.bringToFront();
  await page.goto(`${KC}/admin/master/console/`);
  await keycloakLogin(d, page, "admin", "admin-lab-pass").catch(() => {});
  await page.waitForLoadState("networkidle");
  await d.sleep(2500);
  // A realm's view needs a fresh load: changing only the hash on the open console kept the
  // master realm on screen (probe of 2026-10-20), so each view goes through about:blank.
  for (const view of ["users", "clients"]) {
    await page.goto("about:blank");
    await page.goto(`${KC}/admin/master/console/#/lab/${view}`);
    await page.waitForLoadState("networkidle");
    await d.sleep(4500);
  }
  await page.goto("about:blank");
  await d.focusTerminal();

  await d.cue("kcadm");
  await d.clearScreen();
  await d.session(SERVER);
  await say(d, "kc-lab create realms -s realm=practice-cli -s enabled=true", 2500);
  await say(d, "kc-lab create clients -r practice-cli -s clientId=practice-web -s publicClient=true -s standardFlowEnabled=true", 2500);
  await say(d, "kc-lab create users -r practice-cli -s username=learner -s enabled=true", 2500);
  await say(d, "kc-lab set-password -r practice-cli --username learner --new-password learner-lab-pass", 2000);
  await say(d, "kc-lab create roles -r practice-cli -s name=reader", 2000);
  await say(d, "kc-lab add-roles -r practice-cli --uusername learner --rolename reader", 2000);
  await say(d, "kc-lab get-roles -r practice-cli --uusername learner", 4500);
  await say(d, "kc-lab delete realms/practice-cli", 2500);
  await d.leave();

  await d.cue("discovery");
  await d.clearScreen();
  await d.session(CLIENT);
  await say(d, "curl -fsS http://172.20.20.1:8080/realms/lab/.well-known/openid-configuration | jq '{issuer, authorization_endpoint, token_endpoint, jwks_uri}'", 6500);
  await say(d, "curl -fsS http://172.20.20.1:8080/realms/lab/protocol/openid-connect/certs | jq '.keys[] | {kid, alg, use}'", 6000);
  await d.leave();

  await d.cue("flow");
  await d.focusBrowser();
  await page.bringToFront();
  await page.goto(`${APP}/`);
  await page.waitForLoadState("networkidle");
  await d.sleep(2500);
  await d.click(page.locator("a[href='/login']"));
  await keycloakLogin(d, page, "alice", "alice-lab-pass");
  await d.sleep(2500);
  await page.goto(`${APP}/protected`);
  await page.waitForLoadState("networkidle");
  await d.sleep(3500);
  await d.cue("claims");
  await page.goto(`${APP}/tokens`);
  await page.waitForLoadState("networkidle");
  await d.sleep(7000);

  await d.cue("roles");
  await page.goto(`${APP}/edit`);
  await page.waitForLoadState("networkidle");
  await d.sleep(3500);
  await page.goto(`${APP}/actions`);
  await page.waitForLoadState("networkidle");
  await d.sleep(1500);
  await d.click(page.locator("form[action='/logout'] button"));
  await page.waitForURL(/127\.0\.0\.1:18081\/?$/, { timeout: 60000 });
  await page.waitForLoadState("networkidle");
  await d.sleep(2000);
  await d.click(page.locator("a[href='/login']"));
  await keycloakLogin(d, page, "bob", "bob-lab-pass");
  await d.sleep(1500);
  await page.goto(`${APP}/edit`);
  await page.waitForLoadState("networkidle");
  await d.sleep(5000);
  await page.goto("about:blank");
  await d.focusTerminal();

  await d.cue("service");
  await d.clearScreen();
  await d.session(CLIENT);
  // The service token lives five seconds: the rehearsal showed that typing three long lines at 40 ms a
  // character outlives it (the first introspection was already inactive). The lines that use a token
  // are typed fast, the token is minted right before each use, and the API gets a fresh one.
  const fast = (cmd, read) => d.guest(cmd, { read, delay: 12 });
  await say(d, "P=http://172.20.20.1:8080/realms/lab/protocol/openid-connect", 1000);
  await say(d, "TOKEN=$(curl -fsS \"$P/token\" -d grant_type=client_credentials -d client_id=lab-service -d client_secret=service-lab-secret | jq -r .access_token)", 800);
  await fast("curl -fsS \"$P/token/introspect\" -d client_id=lab-web -d client_secret=web-lab-secret --data-urlencode \"token=$TOKEN\" | jq '{active, client_id, aud, exp}'", 4500);
  await fast("TOKEN=$(curl -fsS \"$P/token\" -d grant_type=client_credentials -d client_id=lab-service -d client_secret=service-lab-secret | jq -r .access_token); curl -si -H \"Authorization: Bearer $TOKEN\" http://172.20.20.2:8000/api | head -1", 3500);
  await fast("sleep 6; curl -fsS \"$P/token/introspect\" -d client_id=lab-web -d client_secret=web-lab-secret --data-urlencode \"token=$TOKEN\" | jq", 5000);

  await d.cue("errors");
  await d.clearScreen();
  await say(d, "curl -si \"$P/token\" -d grant_type=client_credentials -d client_id=lab-service -d client_secret=wrong | head -1", 3000);
  await say(d, "curl -siG \"$P/auth\" --data-urlencode client_id=lab-web --data-urlencode response_type=code --data-urlencode redirect_uri=http://not-allowed.invalid/callback | head -1", 3500);
  await say(d, "curl -si -H \"Authorization: Bearer $TOKEN\" http://172.20.20.2:8000/api", 4500);
  await d.leave();

  await d.cue("journal");
  await d.clearScreen();
  await d.session(SERVER);
  await say(d, "sudo journalctl -u keycloak --no-pager -n 4 | cut -c1-150", 6500);
  await d.leave();

  await d.cue("tests");
  await d.clearScreen();
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
