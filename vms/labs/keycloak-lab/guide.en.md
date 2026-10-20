# Keycloak: from identity to permission to open a page

Two real machines: an identity server and a client running a small web application.
The app never receives Alice's password: she enters it on Keycloak's page. The app
receives proof of her login and a token for accessing a resource.

**OAuth 2.0** describes delegated access to resources. **OpenID Connect (OIDC)**
adds identity: who signed in? Authentication recognizes Alice; authorization decides
whether she may open `/edit`. Bob can sign in successfully and still get 403 there.

| VM | Contents | `keycloak-lan` | RAM | Host access |
|---|---|---|---|---|
| `keycloak-lab-server` | Keycloak **26.4.7**, Java 21, PostgreSQL | 172.20.20.1/24 | 2048 MiB | SSH 2390; console http://127.0.0.1:18080/admin/ |
| `keycloak-lab-client` | Python/Flask app, gunicorn, curl, jq | 172.20.20.2/24 | 512 MiB | SSH 2391; app http://127.0.0.1:18081/ |

Total guest RAM: **2560 MiB**, plus QEMU overhead. No desktop is needed: use the host
browser. Tests run a scripted HTTP browser on the client and submit Keycloak's real
login form. Both guests also have their own NAT interface. PostgreSQL is local to
the server and has no host forward.

These are public example credentials, only for this lab:

| Identity | Password / secret | Purpose |
|---|---|---|
| `admin` | `admin-lab-pass` | administration in `master` |
| `alice` | `alice-lab-pass` | `lab` realm, `editor` role |
| `bob` | `bob-lab-pass` | `lab` realm, no `editor` role |
| client `lab-web` | `web-lab-secret` | confidential web application |
| client `lab-service` | `service-lab-secret` | service without a user |
| PostgreSQL `keycloak` | `db-lab-pass` | local database `keycloak` |

The Linux account follows the profile identity (`lab/lab` without local overrides).
`vmctl shell` uses the project's SSH key.

## Start and understand the addresses

On the host, from the repository (`./bin/vmctl` if it is not on PATH):

```bash
vmctl group install keycloak-lab
vmctl group status keycloak-lab
vmctl group map keycloak-lab --open
vmctl group test keycloak-lab
vmctl shell keycloak-lab-server
# In another terminal:
vmctl shell keycloak-lab-client
```

Open **http://127.0.0.1:18081/** in the host browser. Keycloak's canonical URL is
`http://127.0.0.1:18080`, used in redirects and `iss`. These are **host browser**
addresses: localhost inside a guest means that guest. The app contacts Keycloak
at `http://172.20.20.1:8080` for token exchange, JWKS and introspection, while still
validating the exact canonical issuer. No `/etc/hosts`, bridge or host sudo needed.

Tests run inside the client and translate only these two public URLs to the guests,
standing in for the host's QEMU forwards. They neither rewrite tokens nor bypass
app validation. Redirects to unrelated destinations are refused.

## 1. Installation and server anatomy

On the **server**, inspect the recipe even though provisioning already ran it:

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

The installation steps are explicit and reproducible:

1. Cloud-init installs Ubuntu's `openjdk-21-jre-headless`, `postgresql`, `curl` and
   `jq`. `kc.sh` starts Java; `kcadm.sh` is the administration client.
2. The script downloads the official `keycloak-26.4.7.tar.gz`, checks SHA-256 and
   extracts it to `/opt/keycloak-26.4.7`, linked as `/opt/keycloak`. The digest from
   the official GitHub asset API is
   `aca60ab72ae4d67adc6b8fe7fca5e08d16a6bb5e4a4f2ebb54050e3f5503fd2b`.
3. It creates the system user, PostgreSQL role and database named `keycloak`.
   `db=postgres` and the JDBC URL select PostgreSQL rather than H2.
4. It writes `keycloak.conf`, copies `realm.json` into `data/import`, creates the
   service and runs `kc.sh start-dev --import-realm`. The first start imports `lab`;
   an existing realm is preserved. Restarting does not restore roles or secrets.
5. The service runs as `keycloak`, with a 256–768 MiB Java heap. JVM overhead,
   PostgreSQL and the OS require additional RAM beyond the heap.

Re-read or repeat the installation without deleting the realm:

```bash
bash ~/keycloak-lab/setup-server.sh
```

The full recipe is available, not hidden inside a container. Official distribution:
[Keycloak 26.4.7](https://github.com/keycloak/keycloak/releases/tag/26.4.7).

### Development versus production

This lab deliberately keeps **HTTP and `start-dev`**: a private segment, host
loopback forwards, fake credentials and an observable protocol. HTTP does not
protect passwords, tokens or cookies from someone able to observe this network.
Cookies use `HttpOnly` and `SameSite=Lax`, but not `Secure` because there is no TLS.
Do not expose this configuration as a production service.

Production needs stable DNS/hostname, a valid TLS certificate, private credentials,
PostgreSQL backups and `start` mode. This is a **template to adapt**, not a command
to run against the current HTTP lab:

```bash
# On the future server, after installing a certificate/key readable by keycloak:
sudo systemctl stop keycloak
sudo -u keycloak /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true
sudo -u keycloak /opt/keycloak/bin/kc.sh start --optimized \
  --hostname=https://sso.example.org:8443 --http-enabled=false \
  --https-certificate-file=/etc/keycloak/tls.crt \
  --https-certificate-key-file=/etc/keycloak/tls.key
```

The service would use that same `start --optimized`; the app would use the HTTPS
issuer, its own HTTPS URL, updated callback/post-logout URIs and `Secure` cookies.
Require HTTPS in the realm too. `hostname` sets public URLs; it neither creates a
certificate nor changes the listening port by itself. If a reverse proxy terminates
TLS, restrict the HTTP/proxy configuration to the trusted proxy connection.
Official references: [configuration](https://www.keycloak.org/server/configuration),
[hostname](https://www.keycloak.org/server/hostname), [TLS](https://www.keycloak.org/server/enabletls).

## 2. Realm, client, user and role: console and CLI

A **realm** separates users and settings. `master` administers Keycloak; `lab`
contains our app's users. A **client** is a registered application. A **role** is
an assigned label: our app decides what `editor` allows; Keycloak does not know
what our `/edit` page does.

First use http://127.0.0.1:18080/admin/ with `admin/admin-lab-pass`:

1. Realm selector → **Create realm** → `practice-console` → Create.
2. **Clients → Create client**: OpenID Connect, Client ID `practice-web`. Leave
   Client authentication off (public client), Standard flow on. Set Valid redirect
   URIs to `http://127.0.0.1:18081/callback`, without wildcards. This practice client
   is independent: the installed app uses `lab-web` in `lab`.
3. **Users → Add user**: `learner`. **Credentials → Set password**:
   `learner-lab-pass`, Temporary off to avoid a required password change.
4. **Realm roles → Create role**: `reader`. Open the user's **Role mapping → Assign
   role**, filter realm roles and assign `reader`.
5. Switch to `lab` and inspect `lab-web` (client authentication, secret, exact
   callback, PKCE S256 in advanced settings), `lab-service` (Service accounts
   roles), Alice/Bob and the `editor` role.

Repeat on the **server**, in a separate realm, with the real `kcadm.sh`:

```bash
/opt/keycloak/bin/kcadm.sh config credentials \
  --server http://127.0.0.1:8080 --realm master --user admin --password admin-lab-pass
/opt/keycloak/bin/kcadm.sh get realms --fields realm
# kc-lab wraps login + kcadm + removal of a private temporary config.
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

Find these objects in the console before deleting them. Remove `practice-console`
from Realm settings when finished. Tests create and clean up their own temporary
realm with a trap; they do not silently repair the `lab` fixture.

## 3. Discovery, JWKS and token claims

On the **client**:

```bash
P=http://172.20.20.1:8080/realms/lab/protocol/openid-connect
curl -fsS http://172.20.20.1:8080/realms/lab/.well-known/openid-configuration | jq
curl -fsS "$P/certs" | jq
```

Discovery is the protocol index: issuer, authorization/token endpoints, JWKS and
logout. JWKS holds **public keys**, not secrets; the JWT header's `kid` chooses the
verification key. A JWT has three base64url parts: header, payload, signature.
Encoded does not mean encrypted.

Obtain and decode a service token (exercise 6 explains this grant):

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

Read `iss` (issuer), `sub` (identity), `aud` (recipient: `lab-api`), `iat` and `exp`
(Unix seconds), and `azp` (requesting client). For Alice's roles, log into the app
and open `/tokens`: `realm_access.roles` includes `editor`. The ID token targets
`lab-web` and establishes login; the access token targets `lab-api`. They are not
interchangeable. The audience mapper in `realm.json` explicitly adds `lab-api`;
registering a client alone does not guarantee that audience.

Decoding is not verification. `/api` and protected pages use PyJWT/JWKS to verify
RS256 signatures, exact issuer, audience and expiration. Test 7 changes a payload
without re-signing it and requires 401.

## 4. Authorization Code + PKCE: login, session and logout

In the host browser, open Developer tools → Network → Preserve log. Visit
http://127.0.0.1:18081/, click **Login**, and enter `alice/alice-lab-pass`.

Follow the requests:

1. `/login` sends 302 to `/realms/lab/protocol/openid-connect/auth`, including
   `response_type=code`, `scope=openid`, `state`, `nonce`, and
   `code_challenge_method=S256`. The app keeps a random `code_verifier` and sends
   only its SHA-256 hash. Stealing the code alone is insufficient to redeem it.
2. The password form is submitted to **Keycloak**, which redirects the browser
   to `/callback?code=...&state=...`. The code is short-lived and single-use.
3. The app checks `state` against its login cookie and exchanges the code with
   secret and verifier. It also verifies the ID token's `nonce`. The exchange
   crosses the private segment and does not appear in browser Network requests:
   it is performed by the app server.
4. The browser receives `lab_session`, an opaque ID. Access, ID and refresh tokens
   stay in Python process memory. `/protected` shows Alice; `/tokens` displays
   verified claims, never the refresh token.
5. **Refresh / logout → Logout** submits a CSRF-protected POST, removes the local
   session and redirects to the end-session endpoint with `id_token_hint` and the
   exact registered return URL. Keycloak ends SSO and calls `/backchannel-logout`.

Read `less /opt/keycloak-lab/app.py` on the client. `lab-web` is confidential
because its server can hold a secret; it also requires PKCE. A browser-only SPA
cannot keep that secret and would be a public client. Password grants are disabled:
even the tests use the login form and authorization code.

Click Login again while authenticated. Keycloak reuses its SSO cookie without
asking for the password; the app creates another local session. Test 4 retains
both app cookies and proves that logout invalidates both, including the backchannel.
A subsequent login must show the credentials form. Another browser with an
independent Keycloak session is not necessarily logged out by this single SSO
session's logout.

The app also introspects tokens to apply revocation immediately. Offline JWT
verification alone might accept a signed token until `exp`. The backchannel
verifies a signed logout token and removes matching local sessions. Restarting
the app clears its in-memory sessions; this deliberately small example runs one
gunicorn worker. A distributed app would need shared storage and load limits.

## 5. Roles become authorization

Alice gets **200** at `/edit`. Log out and sign in as `bob/bob-lab-pass`:
`/protected` gives 200, `/edit` gives **403**, `insufficient_role`. **401** means a
valid credential is missing; **403** means the identity is valid but permission
is missing.

On the server:

```bash
kc-lab get-roles -r lab --uusername alice
kc-lab get-roles -r lab --uusername bob
kc-lab remove-roles -r lab --uusername alice --rolename editor
# A new Alice login now gets /edit 403. Test 5 must fail.
# On the host: bash vms/labs/keycloak-lab/tests/test_05_roles.sh
kc-lab add-roles -r lab --uusername alice --rolename editor
# A new login gets 200 again; repeat the test.
```

A role change does not rewrite an already issued JWT. Log in or refresh again to
receive new claims. The console equivalent is Users → Alice → Role mapping.
Restore `editor` before the final test run.

## 6. Client credentials, refresh, expiry and introspection

On the client, get a token **without a user** and use it immediately: it lasts five seconds.

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

Initially: API 200, `active: true`; after expiry: API 401 `ExpiredSignatureError`,
`active: false`. Introspection authenticates the calling client and asks the server
about the token's current state; merely decoding a JWT does not do that. The service
gets a `lab-api` access token without a refresh token and repeats client credentials
when it needs another one.

Alice's access token lasts **120 seconds**. Read `exp` at `/tokens`, open `/actions`,
click Refresh token and read `exp` again (wait at least a second to see it change).
You can leave the `/actions` form open, wait over 120 seconds and then submit
Refresh: the refresh token can work after the access token expires. Do not exceed
the session timeout: 30-minute realm idle timeout, one-hour realm maximum; the app
limits its own local sessions to 30 minutes.

The app submits `POST /token` with `grant_type=refresh_token`, `client_id=lab-web`,
`client_secret=web-lab-secret`, and `refresh_token=...`. Refresh tokens remain on
the app server, outside browser JavaScript and logs. Rotation replaces them after
every refresh; reusing the old one yields `400 invalid_grant`. Test 6 checks real
acquisition, rotation, reuse rejection and revocation after logout.

## 7. Read protocol errors

On the client:

```bash
P=http://172.20.20.1:8080/realms/lab/protocol/openid-connect
curl -si "$P/token" -d grant_type=client_credentials \
  -d client_id=lab-service -d client_secret=wrong
curl -siG "$P/auth" --data-urlencode client_id=lab-web \
  --data-urlencode response_type=code \
  --data-urlencode redirect_uri=http://not-allowed.invalid/callback
```

| Fault | Expected result | Interpretation |
|---|---|---|
| Wrong secret | 401 `unauthorized_client` | app credential, not Alice's password; exact response in 26.4.7 |
| Unregistered redirect | 400 page containing `redirect_uri` | Keycloak must not redirect a code to that destination |
| Wrong PKCE verifier / reused code | 400 `invalid_grant` | code alone is insufficient, or already consumed |
| Genuinely expired bearer | API 401 `ExpiredSignatureError` | compare `exp` and the clock; obtain a new token |
| Modified JWT | API 401 `InvalidSignatureError` | decoding cannot forge a signature |
| Callback without browser-bound state | app 400 `invalid_state` | this browser did not initiate a matching login |
| Missing role | app 403 `insufficient_role` | authentication succeeded, authorization denied |

Logs: `sudo journalctl -u keycloak -n 40 --no-pager` on the server;
`sudo journalctl -u oidc-app -n 40 --no-pager` on the client. Test diagnostics show
the failed assertion, rather than merely probing whether a port is open.

## Tests, reset and shutdown

```bash
# Host:
vmctl group test keycloak-lab
bash vms/labs/keycloak-lab/tests/test_05_roles.sh
python3 -m unittest discover -s tests
vmctl group down keycloak-lab
vmctl group up keycloak-lab
# Discard ONLY this lab's changes and restore its initial checkpoints:
vmctl group reset keycloak-lab
```

`group install` is cumulative and does not erase an altered realm. `group reset`
restores both lab guests and loses completed exercises. Provisioning preserves
existing realms, so a test does not hide configuration errors by resetting them.
Each `test_NN_*.sh` corresponds to exercise NN and uses the repository's standard
helpers. Live evidence, injected faults and remaining limitations are recorded
in [STATO.md](STATO.md).

To reproduce the deliberate faults (only this running lab is changed; each change
is restored by a trap), run `bash vms/labs/keycloak-lab/tests/fault-check.sh`.
It stops Keycloak, disables service accounts, changes each client secret and removes
Alice's role, requires each affected exercise to fail, restores it and requires a pass.
Exercise 4 also runs seven app unit tests inside the guest using real RSA signatures.

Official further reading: [OIDC endpoints](https://www.keycloak.org/securing-apps/oidc-layers),
[administration](https://www.keycloak.org/docs/latest/server_admin/index.html).
Menus can change across releases: this lab pins **26.4.7**, not `latest`.
