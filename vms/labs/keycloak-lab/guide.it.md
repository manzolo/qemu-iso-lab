# Keycloak: dall'identità al permesso di aprire una pagina

Andrea, in questo lab ci sono due macchine vere: un server che conosce le identità e
un client che esegue un'applicazione web. L'app non riceve la password di Alice: la
password viene inserita nella pagina di Keycloak. L'app riceve invece una prova del
login e un token con cui chiedere accesso a una risorsa.

**OAuth 2.0** descrive come ottenere accesso a una risorsa. **OpenID Connect (OIDC)**
aggiunge l'identità dell'utente: «chi ha fatto login?». Autenticazione significa
riconoscere Alice; autorizzazione significa decidere se Alice può aprire `/edit`.
Bob può autenticarsi correttamente e ricevere comunque un 403 su quella pagina.

| VM | Cosa contiene | Segmento `keycloak-lan` | RAM | Accesso dall'host |
|---|---|---|---|---|
| `keycloak-lab-server` | Keycloak **26.4.7**, Java 21, PostgreSQL | 172.20.20.1/24 | 2048 MiB | SSH 2390; console http://127.0.0.1:18080/admin/ |
| `keycloak-lab-client` | app Python/Flask, gunicorn, curl, jq | 172.20.20.2/24 | 512 MiB | SSH 2391; app http://127.0.0.1:18081/ |

Totale guest: **2560 MiB**, più l'overhead di QEMU. Nessun desktop necessario:
il browser dell'host basta; i test usano sul client un browser HTTP programmato
che legge il vero form di Keycloak. Ogni VM ha anche la propria scheda NAT.
Il database ascolta solo sul server, senza forward verso l'host.

Le credenziali sono esempi pubblici, da usare soltanto qui:

| Identità | Password / segreto | A cosa serve |
|---|---|---|
| `admin` | `admin-lab-pass` | console nel realm `master` |
| `alice` | `alice-lab-pass` | realm `lab`, ruolo `editor` |
| `bob` | `bob-lab-pass` | realm `lab`, senza `editor` |
| client `lab-web` | `web-lab-secret` | app web, client confidenziale |
| client `lab-service` | `service-lab-secret` | servizio senza utente |
| PostgreSQL `keycloak` | `db-lab-pass` | database `keycloak`, localhost |

L'utente Linux segue l'identità del profilo (`lab/lab` senza override locali).
`vmctl shell` usa la chiave configurata dal progetto.

## Avvio e indirizzi: perché ce ne sono due

Dalla directory del repository sull'host (`./bin/vmctl` se non è nel PATH):

```bash
vmctl group install keycloak-lab
vmctl group status keycloak-lab
vmctl group map keycloak-lab --open
vmctl group test keycloak-lab
vmctl shell keycloak-lab-server
# In un altro terminale:
vmctl shell keycloak-lab-client
```

Nel browser dell'host apri **http://127.0.0.1:18081/**. Keycloak ha come URL canonico
`http://127.0.0.1:18080`: lo trovi nei redirect e nel claim `iss`. Questi due URL
valgono **dal browser dell'host**; `127.0.0.1` dentro una VM indica quella VM.
L'app contatta invece Keycloak su `http://172.20.20.1:8080` per scambiare codici,
leggere JWKS e fare introspection. Continua a verificare l'issuer canonico esatto.
Non occorre cambiare `/etc/hosts`, configurare bridge o usare sudo sull'host.

I test girano nel client: traducono soltanto i due URL pubblici verso gli indirizzi
delle VM, come farebbero i forward QEMU. Non alterano il contenuto dei token né
saltano la verifica dell'app. Una destinazione di redirect estranea viene rifiutata.

## 1. Installazione e anatomia del server

Sul **server**, leggi il provisioning, anche se è già stato eseguito:

```bash
less ~/keycloak-lab/setup-server.sh
java -version
/opt/keycloak/bin/kc.sh --version
systemctl status keycloak postgresql --no-pager
systemctl cat keycloak
sudo cat /opt/keycloak/conf/keycloak.conf
ss -ltn | grep -E ':(8080|5432) '
sudo -u postgres psql -d keycloak -c 'SELECT name FROM realm;'
sudo journalctl -u keycloak --no-pager -n 30
```

Ecco i passi che rendono l'installazione riproducibile:

1. Cloud-init installa `openjdk-21-jre-headless`, `postgresql`, `curl` e `jq`
   tramite i pacchetti Ubuntu. `kc.sh` avvia Java; `kcadm.sh` è il client amministrativo.
2. Lo script scarica `keycloak-26.4.7.tar.gz` dal rilascio ufficiale GitHub,
   controlla SHA-256 e lo estrae in `/opt/keycloak-26.4.7`, con il link `/opt/keycloak`.
   Il digest registrato dall'API ufficiale dell'asset è
   `aca60ab72ae4d67adc6b8fe7fca5e08d16a6bb5e4a4f2ebb54050e3f5503fd2b`.
3. Crea l'utente di sistema `keycloak`, il ruolo PostgreSQL e il database omonimi.
   `db=postgres` e la URL JDBC scelgono questo database, non H2.
4. Scrive `keycloak.conf`, copia `realm.json` in `data/import`, crea il servizio
   e avvia `kc.sh start-dev --import-realm`. Il primo avvio importa il realm `lab`;
   un realm già presente viene preservato. Riavviare non ripristina ruoli o segreti.
5. Il servizio gira come `keycloak`, con heap Java da 256 a 768 MiB. Memoria del
   processo e RAM della VM sono maggiori dell'heap: includono JVM, PostgreSQL e OS.

Per seguire nuovamente i passi senza eliminare dati, sul server:

```bash
bash ~/keycloak-lab/setup-server.sh
```

La ricetta esatta è leggibile; non c'è un contenitore opaco. La distribuzione
ufficiale è qui: [Keycloak 26.4.7](https://github.com/keycloak/keycloak/releases/tag/26.4.7).

### Da sviluppo a produzione

Nel lab restiamo deliberatamente su **HTTP e `start-dev`**: segmento privato,
forward solo sul loopback dell'host, credenziali finte e possibilità di osservare
il protocollo. HTTP non protegge password, token e cookie da chi può osservare
questa rete. I cookie sono `HttpOnly` e `SameSite=Lax`, ma non `Secure` perché qui
non c'è HTTPS. Questo non è un modello da esporre in rete.

In produzione servono DNS e hostname stabili, TLS con certificato valido, segreti
propri, backup PostgreSQL e modalità `start`. Per capire il passaggio, questo è
uno **schema da adattare**, non un comando da eseguire nel lab attuale:

```bash
# Sul futuro server, con certificato e chiave già installati e leggibili da keycloak:
sudo systemctl stop keycloak
sudo -u keycloak /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true
sudo -u keycloak /opt/keycloak/bin/kc.sh start --optimized \
  --hostname=https://sso.example.org:8443 --http-enabled=false \
  --https-certificate-file=/etc/keycloak/tls.crt \
  --https-certificate-key-file=/etc/keycloak/tls.key
```

Poi il servizio userà quello stesso `start --optimized`; l'app userà il nuovo
issuer HTTPS, un proprio URL HTTPS, callback e post-logout URI aggiornati e cookie
`Secure`. Nel realm si richiederà HTTPS. `hostname` stabilisce gli URL pubblici,
non genera certificati e non cambia da solo la porta d'ascolto. Se TLS termina
su un reverse proxy, la configurazione HTTP/proxy va limitata al collegamento
fidato verso quel proxy. Riferimenti ufficiali:
[configurazione](https://www.keycloak.org/server/configuration),
[hostname](https://www.keycloak.org/server/hostname),
[TLS](https://www.keycloak.org/server/enabletls).

## 2. Realm, client, utente, ruolo: console e riga di comando

Un **realm** è uno spazio separato di utenti e impostazioni. `master` amministra
Keycloak; `lab` contiene gli utenti dell'app. Un **client** è un'applicazione
registrata; il **ruolo** è un'etichetta assegnata all'utente. È l'app a decidere
cosa significhi `editor`, non Keycloak a conoscere la nostra pagina `/edit`.

Prima prova la console http://127.0.0.1:18080/admin/ con `admin/admin-lab-pass`:

1. Dal selettore dei realm, **Create realm** → `practice-console` → Create.
2. **Clients → Create client**: tipo OpenID Connect, Client ID `practice-web`.
   Lascia Client authentication disattivata (client pubblico), Standard flow
   attivo. Come Valid redirect URIs inserisci `http://127.0.0.1:18081/callback`.
   Non usare `*`. È un client di esercizio: l'app installata usa `lab-web` in `lab`.
3. **Users → Add user**: `learner`, poi **Credentials → Set password**:
   `learner-lab-pass`, Temporary disattivato per non richiedere un cambio al login.
4. **Realm roles → Create role**: `reader`. Nell'utente, **Role mapping → Assign
   role**, filtra i ruoli del realm e assegna `reader`.
5. Torna al realm `lab`. Esamina `lab-web` (autenticazione client attiva, secret,
   callback esatta, PKCE S256 nelle impostazioni avanzate), `lab-service`
   (Service accounts roles), gli utenti Alice/Bob e il ruolo `editor`.

Adesso ripeti dal **server** in un secondo realm, usando il vero `kcadm.sh`:

```bash
/opt/keycloak/bin/kcadm.sh config credentials \
  --server http://127.0.0.1:8080 --realm master --user admin --password admin-lab-pass
/opt/keycloak/bin/kcadm.sh get realms --fields realm
# kc-lab è un piccolo wrapper leggibile: login + kcadm + rimozione config temporanea.
cat /usr/local/bin/kc-lab
kc-lab create realms -s realm=practice-cli -s enabled=true
kc-lab create clients -r practice-cli -s clientId=practice-web \
  -s publicClient=true -s standardFlowEnabled=true \
  -s 'redirectUris=["http://127.0.0.1:18081/callback"]'
kc-lab create users -r practice-cli -s username=learner -s enabled=true
kc-lab set-password -r practice-cli --username learner --new-password learner-lab-pass
kc-lab create roles -r practice-cli -s name=reader
kc-lab add-roles -r practice-cli --uusername learner --rolename reader
kc-lab get-roles -r practice-cli --uusername learner
kc-lab remove-roles -r practice-cli --uusername learner --rolename reader
kc-lab get-roles -r practice-cli --uusername learner
kc-lab delete realms/practice-cli
```

Ritrova nella console gli oggetti appena creati prima di eliminarli. Puoi eliminare
anche `practice-console` da Realm settings. I test creano un proprio realm temporaneo
e lo cancellano con una trap; non aggiustano silenziosamente i dati del realm `lab`.

## 3. Discovery, JWKS e contenuto di un token

Sul **client**:

```bash
P=http://172.20.20.1:8080/realms/lab/protocol/openid-connect
curl -fsS http://172.20.20.1:8080/realms/lab/.well-known/openid-configuration | jq
curl -fsS "$P/certs" | jq
```

Discovery è l'indice del protocollo: issuer, authorization endpoint, token endpoint,
JWKS e logout. JWKS contiene **chiavi pubbliche**, non segreti: `kid` nell'intestazione
JWT sceglie la chiave con cui verificare la firma. Un JWT ha tre parti base64url:
header, payload, firma. Codificato non significa cifrato.

Ottieni un token di servizio e decodificalo (l'esercizio 6 spiega questo grant):

```bash
TOKEN=$(curl -fsS "$P/token" -d grant_type=client_credentials \
  -d client_id=lab-service -d client_secret=service-lab-secret | jq -r .access_token)
export TOKEN
python3 - <<'PY'
import os, json, base64
header, payload, signature = os.environ['TOKEN'].split('.')
for part in (header, payload):
    print(json.dumps(json.loads(base64.urlsafe_b64decode(part + '===')), indent=2))
PY
```

Leggi `iss` (chi lo emette), `sub` (identità), `aud` (destinatario: `lab-api`), `iat`
e `exp` (secondi Unix), `azp` (client che ha richiesto il token). Per i ruoli di
Alice, fai login nell'app e apri `/tokens`: `realm_access.roles` include `editor`.
L'ID token ha audience `lab-web`: serve all'app per il login. L'access token è per
l'API `lab-api`; non sostituire l'uno all'altro. Il mapper audience in `realm.json`
aggiunge esplicitamente `lab-api`; registrare un client da solo non lo garantisce.

Decodificare non dimostra autenticità. `/api` e le pagine protette verificano con
PyJWT e JWKS: algoritmo consentito RS256, firma, issuer esatto, audience e scadenza.
Il test 7 cambia il payload senza rifirmarlo e pretende un 401.

## 4. Authorization Code + PKCE: login, sessione, logout

Apri il browser dell'host, strumenti sviluppatore → Network → Preserve log, quindi
http://127.0.0.1:18081/ e **Login**. Usa `alice/alice-lab-pass`.

Segui le richieste, in ordine:

1. `/login` risponde 302 verso `/realms/lab/protocol/openid-connect/auth`.
   Ci sono `response_type=code`, `scope=openid`, `state`, `nonce` e
   `code_challenge_method=S256`. L'app conserva un `code_verifier` casuale e invia
   soltanto il suo hash SHA-256. Rubare il codice non basta per riscattarlo.
2. Il form con la password viene inviato a **Keycloak**, che fa login e rinvia
   il browser a `/callback?code=...&state=...`. Il codice dura poco ed è monouso.
3. L'app verifica `state` legato al cookie di login e scambia il codice sul
   token endpoint con secret e verifier. Controlla anche `nonce` nell'ID token.
   Questa richiesta avviene sul segmento privato: non appare nella scheda Network
   del browser, perché la esegue il server dell'app.
4. Il browser riceve `lab_session`, un identificatore opaco. Access, ID e refresh
   token restano nella memoria del processo Python. `/protected` mostra Alice;
   `/tokens` mostra i claim verificati, mai il refresh token.
5. **Refresh / logout → Logout** invia un POST con CSRF, elimina la sessione locale
   e manda il browser all'end-session endpoint con `id_token_hint` e ritorno esatto.
   Keycloak chiude la sessione SSO e chiama `/backchannel-logout` sull'app.

Leggi il codice sul client: `less /opt/keycloak-lab/app.py`. `lab-web` è confidenziale
perché l'app può custodire un segreto sul server; usa comunque PKCE. Una SPA che gira
solo nel browser non può custodire quel segreto e dovrebbe essere un client pubblico.
Il password grant è disabilitato: anche i test attraversano il form e il codice.

Per osservare SSO, fai di nuovo Login mentre sei già autenticato: Keycloak riusa
il suo cookie e non richiede la password. L'app crea una nuova sessione locale.
Il test 4 conserva entrambi i cookie dell'app e dimostra che un logout li invalida
entrambi, verificando anche il backchannel. Una nuova richiesta di login deve
mostrare di nuovo il form. Un altro browser con una sessione Keycloak indipendente
non viene necessariamente disconnesso da questo logout della singola sessione SSO.

L'app usa anche introspection per applicare subito la revoca; la sola validazione
JWT offline potrebbe accettare un token firmato fino a `exp`. Il backchannel
verifica la firma del logout token e rimuove le sessioni corrispondenti. Un riavvio
dell'app svuota le sessioni in memoria: è voluto per un esempio piccolo, con un solo
worker gunicorn. Un'app distribuita userebbe uno store condiviso e limiti di carico.

## 5. Dal ruolo all'autorizzazione

Con Alice, `/edit` dà **200**. Fai logout, accedi con `bob/bob-lab-pass`: `/protected`
dà 200 ma `/edit` dà **403**, `insufficient_role`. **401** significa che manca una
credenziale valida; **403** significa che l'identità è valida ma il permesso manca.

Sul server:

```bash
kc-lab get-roles -r lab --uusername alice
kc-lab get-roles -r lab --uusername bob
kc-lab remove-roles -r lab --uusername alice --rolename editor
# Nuovo login di Alice: /edit ora dà 403. Il test 5 deve fallire.
# Nell'host: bash vms/labs/keycloak-lab/tests/test_05_roles.sh
kc-lab add-roles -r lab --uusername alice --rolename editor
# Nuovo login: /edit torna 200; ripeti il test.
```

La modifica non riscrive un token già emesso. Fai un nuovo login o refresh per
ricevere i nuovi claim. Dalla console ottieni lo stesso risultato in Users →
Alice → Role mapping. Ricorda di rimettere `editor` prima della verifica finale.

## 6. Client credentials, refresh token, scadenza e introspection

Sul client ottieni un token **senza un utente** e usalo subito (dura cinque secondi):

```bash
P=http://172.20.20.1:8080/realms/lab/protocol/openid-connect
TOKEN=$(curl -fsS "$P/token" -d grant_type=client_credentials \
  -d client_id=lab-service -d client_secret=service-lab-secret | jq -r .access_token)
curl -si -H "Authorization: Bearer $TOKEN" http://172.20.20.2:8000/api
curl -fsS "$P/token/introspect" -d client_id=lab-web -d client_secret=web-lab-secret \
  --data-urlencode "token=$TOKEN" | jq
sleep 6
curl -si -H "Authorization: Bearer $TOKEN" http://172.20.20.2:8000/api
curl -fsS "$P/token/introspect" -d client_id=lab-web -d client_secret=web-lab-secret \
  --data-urlencode "token=$TOKEN" | jq
```

Prima: API 200, introspection `active: true`; dopo: API 401 `ExpiredSignatureError`,
`active: false`. Introspection è una domanda autenticata al server sullo stato
attuale del token; non è la stessa cosa che decodificare il JWT. Il servizio
riceve un access token per `lab-api` senza refresh token: quando scade, ripete
client credentials con il proprio segreto.

Per Alice l'access token dura **120 secondi**. Apri `/tokens` e annota `exp`, poi
`/actions` → Refresh token e rileggi `exp` (aspetta almeno un secondo per vederlo
cambiare). Puoi lasciare il form `/actions` aperto, attendere oltre 120 secondi e
inviare Refresh: il refresh token può ancora funzionare dopo la scadenza dell'access
token. Non aspettare oltre il timeout della sessione (30 minuti di inattività,
massimo un'ora nel realm; sessione locale dell'app limitata a 30 minuti).

La richiesta che l'app invia è `POST /token` con `grant_type=refresh_token`,
`client_id=lab-web`, `client_secret=web-lab-secret`, `refresh_token=...`. Il refresh
token resta sul server dell'app; non lo mettiamo nel browser o nei log. La rotazione
è attiva: viene sostituito a ogni refresh, e riusare il precedente dà
`400 invalid_grant`. Il test 6 verifica acquisizione, rotazione, rifiuto del riuso
e revoca dopo logout contro Keycloak reale.

## 7. Leggere gli errori senza indovinare

Sul client:

```bash
P=http://172.20.20.1:8080/realms/lab/protocol/openid-connect
curl -si "$P/token" -d grant_type=client_credentials \
  -d client_id=lab-service -d client_secret=wrong
curl -siG "$P/auth" --data-urlencode client_id=lab-web \
  --data-urlencode response_type=code \
  --data-urlencode redirect_uri=http://not-allowed.invalid/callback
```

| Guasto | Esito atteso | Come leggerlo |
|---|---|---|
| Segreto errato | 401 `unauthorized_client` | credenziale dell'app, non password di Alice |
| Redirect non registrato | 400, pagina con `redirect_uri` | Keycloak non deve rinviare un codice a quella destinazione |
| Verifier PKCE errato / codice riusato | 400 `invalid_grant` | il codice non basta o è già consumato |
| Bearer realmente scaduto | API 401 `ExpiredSignatureError` | confronta `exp` e orologio, rinnova il token |
| JWT modificato | API 401 `InvalidSignatureError` | decodificare non permette di falsificare la firma |
| Callback senza stato legato al browser | app 400 `invalid_state` | manca un login iniziato da quel browser |
| Ruolo mancante | app 403 `insufficient_role` | autenticazione riuscita, autorizzazione negata |

Log: `sudo journalctl -u keycloak -n 40 --no-pager` sul server,
`sudo journalctl -u oidc-app -n 40 --no-pager` sul client. I test stampano l'assertion
che è fallita, non si limitano a cercare una porta aperta.

## Verifiche, ripristino e arresto

```bash
# Sull'host:
vmctl group test keycloak-lab
bash vms/labs/keycloak-lab/tests/test_05_roles.sh
python3 -m unittest discover -s tests
vmctl group down keycloak-lab
# Per riaccendere:
vmctl group up keycloak-lab
# Per perdere SOLO le modifiche di questo lab e tornare ai checkpoint iniziali:
vmctl group reset keycloak-lab
```

`group install` è cumulativo e non cancella un realm alterato. `group reset` ripristina
entrambi i guest del lab e perde gli esercizi svolti. Il provisioning del server
preserva un realm esistente, quindi non nasconde un errore nei test.
Ogni `test_NN_*.sh` corrisponde all'esercizio NN e viene eseguito con gli helper
standard del repository. Le prove reali, i guasti introdotti e i limiti ancora
presenti sono registrati in [STATO.md](STATO.md).

Per riprodurre i guasti intenzionali, sul lab acceso esegui
`bash vms/labs/keycloak-lab/tests/fault-check.sh`: modifica soltanto questo lab e
ripristina ogni modifica con una trap. Ferma Keycloak, disabilita gli account di
servizio, cambia i due segreti e toglie il ruolo ad Alice; pretende un fallimento
del test interessato e poi un PASS dopo il ripristino. L’esercizio 4 esegue anche
sette test unitari dell’app nel guest, con firme RSA reali.

Approfondimenti ufficiali: [endpoint OIDC](https://www.keycloak.org/securing-apps/oidc-layers),
[amministrazione](https://www.keycloak.org/docs/latest/server_admin/index.html).
I menu possono cambiare tra versioni: questo lab fissa **26.4.7**, non `latest`.
