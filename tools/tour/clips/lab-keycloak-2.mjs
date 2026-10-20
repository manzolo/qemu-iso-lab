export const title = { en: "Lesson 2: the pact between the app and Keycloak", it: "Lezione 2: il patto tra l'app e Keycloak" };
export const series = "labs";
export const lab = "keycloak-lab";
export const order = 2;

const LAB = "keycloak-lab";
const SERVER = "keycloak-lab-server", CLIENT = "keycloak-lab-client";
const CHECKOUT = "~/lab/demo/qemu-iso-lab";
const KC = "http://127.0.0.1:18080";    // Keycloak, forwarded to the studio's loopback
const APP = "http://127.0.0.1:18081";   // the OIDC app on the client, same forward

// Asked by Manzolo after lesson 1 (2026-10-20): not only curl, the small web app: where the return
// address and the logout address are configured, on both sides, what happens when they do not match,
// and the token opened piece by piece. The lab is installed off camera in setup(): this lesson
// starts where the first one ended.
export const cues = [
  { id: "intro", en: "In lesson one you saw the login. Now the pact between the app and Keycloak: where each of the two writes its addresses, what happens when they disagree, and what is really inside a token. The lab is already installed.",
    it: "Nella prima lezione hai visto il login. Ora il patto tra l'app e Keycloak: dove ognuno scrive gli indirizzi, cosa succede se non combaciano, e che cosa c'è davvero dentro un token. Il lab è già installato." },
  { id: "appside", en: "The app is one Python file. On its side it knows three things: who the issuer is, Keycloak; who it is, lab-web; and the addresses it wants to come back to, after login and after logout.",
    it: "L'app è un file Python. Dal suo lato sa tre cose: chi è l'emittente, Keycloak; chi è lei, lab-web; e a quali indirizzi vuole tornare, dopo il login e dopo il logout." },
  { id: "kcside", en: "On Keycloak's side, the card of the client lab-web says the same things: the return addresses allowed after login, and after logout. They must match letter for letter.",
    it: "Dal lato di Keycloak, la scheda del client lab-web dice le stesse cose: gli indirizzi di ritorno ammessi dopo il login, e dopo il logout. Devono combaciare lettera per lettera." },
  { id: "secret", en: "And under Credentials, the secret the app uses to introduce itself to Keycloak: the same one written in the Python file.",
    it: "E sotto Credentials, il segreto con cui l'app si presenta a Keycloak: lo stesso scritto nel file Python." },
  { id: "cliget", en: "From the command line the same card is read with kcadm: first the client's id, then its two addresses.",
    it: "Da riga di comando la stessa scheda si legge con kcadm: prima l'id del client, poi i suoi due indirizzi." },
  { id: "break", en: "Let us break the pact. I change the allowed return address: no longer callback, but altrove. The app, on its side, knows nothing about it.",
    it: "Rompiamo il patto. Cambio l'indirizzo di ritorno ammesso: non più callback, ma altrove. L'app, dal suo lato, non ne sa niente." },
  { id: "refused", en: "Login from the app: Keycloak stops before even asking for a password. Invalid redirect address. The access code never goes to an address that was not registered.",
    it: "Login dall'app: Keycloak si ferma prima ancora di chiedere la password. Indirizzo di ritorno non valido. Il codice di accesso non finisce mai a un indirizzo che non è stato registrato." },
  { id: "fix", en: "I put callback back, and the pact holds again.",
    it: "Rimetto callback, e il patto torna." },
  { id: "loginok", en: "Login as Alice: in again.",
    it: "Login come Alice: di nuovo dentro." },
  { id: "claims", en: "From inside, the app shows the claims of Alice's token, already verified: who she is, who issued it, for whom, until when, and her roles.",
    it: "Da dentro, l'app mostra i claim del token di Alice, già verificati: chi è, chi l'ha emesso, per chi, fino a quando, e i suoi ruoli." },
  { id: "logout", en: "Logout goes through Keycloak too: the app sends the browser there with the token in hand, Keycloak closes the session and sends it back to the registered exit address. Without that address, it would stop.",
    it: "Anche il logout passa da Keycloak: l'app ci manda il browser con il token in mano, Keycloak chiude la sessione e lo rimanda all'indirizzo di uscita registrato. Senza quell'indirizzo, si fermerebbe." },
  { id: "lifespan", en: "Now let us open a token. The service token in the lab lives five seconds, too few to look at it: with kcadm I give it five minutes. This too is one line in the client's card.",
    it: "Ora apriamo un token. Quello di servizio nel lab vive cinque secondi, troppo pochi per guardarlo: con kcadm gli do cinque minuti. Anche questa è una riga nella scheda del client." },
  { id: "raw", en: "From the client I ask for a token and look at it raw: a long string, three pieces separated by a dot. Header, payload, signature. Here are the first sixty characters of each.",
    it: "Dal client chiedo un token e lo guardo grezzo: una stringa lunga, tre pezzi separati da un punto. Intestazione, contenuto, firma. Qui ne vedi i primi sessanta caratteri." },
  { id: "header", en: "The first piece is just base64, not encrypted: it says which algorithm signed it and with which key, kid. The key the app fetches from the discovery.",
    it: "Il primo pezzo è solo base64, non è cifrato: dice con quale algoritmo è firmato e con quale chiave, kid. La chiave che l'app va a prendere dalla discovery." },
  { id: "payload", en: "The second piece is the content, readable by anyone too. Issued by, for whom, by which app, when, until when, and the roles. A token hides nothing: it is no place for secrets.",
    it: "Il secondo pezzo è il contenuto, anche questo leggibile da chiunque. Emesso da, per chi, da quale app, quando, fino a quando, e i ruoli. Un token non nasconde niente: non è il posto per i segreti." },
  { id: "forge", en: "So I forge one: I rewrite the content with one more hour of life, and put it back between the same header and the same signature.",
    it: "Allora lo falsifico: riscrivo il contenuto con un'ora di vita in più, e lo rimetto tra la stessa intestazione e la stessa firma." },
  { id: "verdict", en: "I ask Keycloak about both tokens: the real one is active, the forged one is not. The signature covers the content: change one character and it no longer matches. That is why the app can trust what it reads.",
    it: "Chiedo a Keycloak dei due token: il vero è attivo, il falso no. La firma copre il contenuto: cambi un carattere, e non torna più. Per questo l'app può fidarsi di quello che legge." },
  { id: "end", top: true, en: "In the guide: exercises 3, 4 and 7, discovery and claims, the login and logout round trip, the errors.",
    it: "Nella guida: esercizi 3, 4 e 7, discovery e claim, il giro del login e del logout, gli errori." },
];

export async function setup(d) {
  // The lab installed before the camera rolls (lesson 1 shows the install), Keycloak listening
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl stop ${CLIENT} >/dev/null 2>&1; ./bin/vmctl stop ${SERVER} >/dev/null 2>&1; ./bin/vmctl group clean ${LAB} --yes >/dev/null 2>&1; true`);
  d.vm(`cd ${CHECKOUT} && ./bin/vmctl group install ${LAB} --yes >/tmp/install.log 2>&1`);
  d.offCamera(SERVER, "for i in $(seq 1 150); do curl -fsS http://127.0.0.1:8080/realms/lab/.well-known/openid-configuration >/dev/null 2>&1 && break; sleep 2; done");
  d.offCamera(CLIENT, "for i in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:8000/ && break; sleep 2; done");
  const ctx = d.page.context();
  for (const p of ctx.pages()) if (p !== d.page && p.url() !== "about:blank") await p.close();
  await ctx.clearCookies();
  await d.page.goto("about:blank");
  await d.openTerminal();
  d.vm("DISPLAY=:0 ~/lab/video/mv.py 1550 120 0.1");
}

const say = (d, cmd, read = 3000) => d.guest(cmd, { read });

// Keycloak's login form (realm lab): username, password, Sign in; back to the app when done.
async function keycloakLogin(d, page, user, password, back = /127\.0\.0\.1:18081/) {
  await page.locator("#username").waitFor({ timeout: 60000 });
  await d.sleep(800);
  await d.click(page.locator("#username"));
  await d.type(user);
  await d.click(page.locator("#password"));
  await d.type(password);
  await d.click(page.locator("#kc-login"));
  await page.waitForURL(back, { timeout: 60000 });
  await page.waitForLoadState("networkidle");
}
const open = async (d, page, url, ms) => { await page.goto(url); await page.waitForLoadState("networkidle"); await d.sleep(ms); };
const toBrowser = async (d, page) => { await d.focusBrowser(); await page.bringToFront(); };
const toTerminal = async (d, page) => { await page.goto("about:blank"); await d.focusTerminal(); };

export async function run(d) {
  const page = d.page;
  await d.cue("intro");
  await d.run("cd qemu-iso-lab", { record: false });
  await d.clearScreen();
  await d.sleep(12000);

  // The app's side: its file
  await d.cue("appside");
  await d.session(CLIENT);
  await say(d, "grep -n 'ISSUER =\\|APP_URL =\\|CLIENT =\\|redirect_uri=\\|post_logout' /opt/keycloak-lab/app.py", 7000);
  await d.leave();

  // Keycloak's side: the client's card in the console
  await d.cue("kcside");
  await toBrowser(d, page);
  await open(d, page, `${KC}/admin/master/console/#/lab/clients`, 500);
  await keycloakLogin(d, page, "admin", "admin-lab-pass", /admin\/master\/console/).catch(() => {});
  await page.waitForLoadState("networkidle");
  await d.sleep(1500);
  await d.click(page.getByRole("link", { name: "lab-web", exact: true }));
  await page.waitForLoadState("networkidle");
  await d.sleep(1500);
  await page.getByText("Valid post logout redirect URIs", { exact: true }).scrollIntoViewIfNeeded();
  await d.sleep(7000);
  await d.cue("secret");
  await d.click(page.getByRole("tab", { name: "Credentials" }));
  await page.waitForLoadState("networkidle");
  await d.sleep(5000);

  // The same card from the command line, then the pact broken and mended
  await d.cue("cliget");
  await toTerminal(d, page);
  await d.clearScreen();
  await d.session(SERVER);
  await say(d, "ID=$(kc-lab get clients -r lab -q clientId=lab-web --fields id --format csv --noquotes)", 1500);
  await say(d, "kc-lab get clients/$ID -r lab --fields 'redirectUris,attributes(post.logout.redirect.uris)'", 5000);
  await d.cue("break");
  await say(d, "kc-lab update clients/$ID -r lab -s 'redirectUris=[\"http://127.0.0.1:18081/altrove\"]'", 3000);
  await d.cue("refused");
  await toBrowser(d, page);
  await open(d, page, `${APP}/`, 1500);
  await d.click(page.locator("a[href='/login']"));
  await page.getByText("Invalid parameter: redirect_uri").waitFor({ timeout: 60000 });
  await d.sleep(8000);
  await d.cue("fix");
  await toTerminal(d, page);
  await say(d, "kc-lab update clients/$ID -r lab -s 'redirectUris=[\"http://127.0.0.1:18081/callback\"]'", 2500);

  // Alice again: in, her claims, out through Keycloak
  await d.cue("loginok");
  await toBrowser(d, page);
  await open(d, page, `${APP}/`, 800);
  await d.click(page.locator("a[href='/login']"));
  await keycloakLogin(d, page, "alice", "alice-lab-pass");
  await d.sleep(1500);
  await d.cue("claims");
  await open(d, page, `${APP}/tokens`, 1500);
  await d.key("Tab"); await d.sleep(300); await d.key("space");  // Chromium's JSON viewer: its Pretty-print box is not in the page's DOM
  await d.sleep(9000);
  await d.cue("logout");
  await open(d, page, `${APP}/actions`, 1500);
  await d.click(page.locator("form[action='/logout'] button"));
  await page.waitForURL(/127\.0\.0\.1:18081\/?$/, { timeout: 60000 });
  await page.waitForLoadState("networkidle");
  await d.sleep(9000);

  // A token opened piece by piece
  await d.cue("lifespan");
  await toTerminal(d, page);
  await d.clearScreen();
  await say(d, "SID=$(kc-lab get clients -r lab -q clientId=lab-service --fields id --format csv --noquotes)", 1500);
  await say(d, "kc-lab update clients/$SID -r lab -s 'attributes.\"access.token.lifespan\"=300'", 3000);
  await d.leave();
  await d.cue("raw");
  await d.clearScreen();
  await d.session(CLIENT);
  await say(d, "P=http://172.20.20.1:8080/realms/lab/protocol/openid-connect", 800);
  await say(d, "TOKEN=$(curl -fsS \"$P/token\" -d grant_type=client_credentials -d client_id=lab-service -d client_secret=service-lab-secret | jq -r .access_token)", 1500);
  await say(d, "echo \"$TOKEN\" | tr . '\\n' | cut -c1-60", 6000);
  await d.cue("header");
  await say(d, "echo \"$TOKEN\" | jq -R 'split(\".\")[0] | gsub(\"-\";\"+\") | gsub(\"_\";\"/\") | @base64d | fromjson'", 7000);
  await d.cue("payload");
  await d.clearScreen();
  await say(d, "echo \"$TOKEN\" | jq -R 'split(\".\")[1] | gsub(\"-\";\"+\") | gsub(\"_\";\"/\") | @base64d | fromjson | {iss, aud, azp, iat, exp, realm_access}'", 9000);
  await d.cue("forge");
  await d.clearScreen();
  await say(d, "C=$(cut -d. -f2 <<<\"$TOKEN\" | tr '_-' '/+' | base64 -d 2>/dev/null | jq -c '.exp += 3600' | base64 -w0 | tr '+/' '-_' | tr -d =)", 1500);
  await say(d, "FALSO=\"$(cut -d. -f1 <<<\"$TOKEN\").$C.$(cut -d. -f3 <<<\"$TOKEN\")\"", 1500);
  await d.cue("verdict");
  await say(d, "for t in \"$TOKEN\" \"$FALSO\"; do curl -fsS \"$P/token/introspect\" -d client_id=lab-web -d client_secret=web-lab-secret --data-urlencode \"token=$t\" | jq -c '{active, exp}'; done", 8000);
  await d.leave();
  await d.cue("end");
  await d.sleep(2000);
}
