# Stato del lavoro — keycloak-lab

Ramo: `keycloak-lab`. Nessun push, nessun sudo sull'host, nessuna modifica alle altre VM.

## Progetto (2026-10-20)

- Letti AGENTS.md, CLAUDE.md, docs/LABS.md e i modelli ssh-lab/mysql-lab.
- Due guest Ubuntu cloud: server 2048 MiB (Keycloak 26.4.7 ufficiale,
  Java 21, PostgreSQL), client 512 MiB (app Python, curl).
- Segmento dedicato keycloak-lan; Authorization Code + S256 PKCE, verifica
  firma/issuer/audience/scadenza, ruoli e logout. HTTP deliberato per il lab locale.
- Sette esercizi con test reali, guide IT/EN, prove di guasti e ripristino.

## Verificato

- Ramo corretto, working tree inizialmente pulito; KVM accessibile e RAM disponibile.
- Release ufficiale: https://github.com/keycloak/keycloak/releases/tag/26.4.7

## Da fare / prossimo passo

Implementare provisioning, app e profili; installare con `./bin/vmctl group install
keycloak-lab`. Poi esercizi, guide, `group test`, guasti intenzionali, suite unittest.
Questo file viene aggiornato a ogni commit significativo con comandi ed esiti reali.

## Prima installazione reale

- `make check` di riferimento: PASS, 1557 pytest + 1748 subtest,
  47 skip; dashboard 59 test PASS (un warning di thread preesistente).
- `./bin/vmctl group install keycloak-lab`: PASS. Entrambi i guest installati
  via cloud image, checkpoint lab-start creati e VM accese. Archivio ufficiale
  Keycloak verificato con SHA-256 dell'asset GitHub. Nessun sudo sull'host.
- Provisioning e app implementati in working tree, sette esercizi e relativi
  test in preparazione. Prossimo passo: eseguire i test reali e scrivere le guide.

## Implementazione e verifiche del lab

- Profili su `ubuntu-cloud-base`, segmentazione `keycloak-lan` 172.20.20.0/24,
  SSH 2390/2391, forward host 18080/18081; RAM 2048 + 512 MiB.
- Keycloak 26.4.7 nativo + Java 21 + PostgreSQL; hash dell'archivio verificato.
  App Python con Authorization Code/S256 PKCE, state/nonce, verifica RSA/JWKS,
  issuer/audience/exp, cookie opaco, CSRF, ruoli, refresh e backchannel logout.
- Sette esercizi, guide IT/EN e test Bash conformi a `_common.sh`.
- `./bin/vmctl group test keycloak-lab`: PASS 7/7, inizialmente 14 check;
  aggiunti poi 7 test unitari dell'app all'esercizio 4 (15 check del runner).
- `python3 -m unittest tests.test_keycloak_app -v`: 7 PASS sull'host.
  Stessi 7 test PASS nel guest contro `/opt/keycloak-lab/app.py`.
- `./bin/vmctl group map keycloak-lab`: PASS.
- Chromium headless reale sull'host: PASS Alice login, /edit 200, refresh,
  logout, Bob login, /edit 403, logout; console admin caricata dopo login.
- Memoria osservata a riposo: server ~847 MiB usati, processo Keycloak ~540 MiB;
  client ~160 MiB usati. RAM allocata totale sempre 2560 MiB.

### Guasti introdotti e ripristinati realmente

`bash vms/labs/keycloak-lab/tests/fault-check.sh`: **28/28 check PASS**.
Ogni test ha prima passato il baseline, poi prodotto un vero `[FAIL]` con uscita
nonzero nel caso alterato, poi passato dopo il ripristino:

| Esercizio | Modifica intenzionale |
|---|---|
| 01 | arrestato `keycloak.service` |
| 02 | disabilitato `serviceAccountsEnabled` di `lab-service` |
| 03 | cambiato il segreto reale di `lab-service` |
| 04 | cambiato il segreto reale di `lab-web`, fallisce lo scambio del codice |
| 05 | tolto `editor` ad Alice, login valido ma `/edit` restituisce 403 |
| 06 | cambiato il segreto reale di `lab-service` |
| 07 | cambiato il segreto reale di `lab-service` |

Ulteriori casi negativi ordinari: redirect non ammesso, verifier errato,
codice riusato, refresh token riusato, JWT alterato, token realmente scaduto,
state/CSRF errati; unit test con issuer/audience/nonce errati e logout replay.
Il test SSO controlla la rimozione locale via backchannel *prima* di introspection.

### Correzioni emerse dalle prove

- Keycloak 26.4.7 restituisce `401 unauthorized_client` per il segreto errato;
  documentazione e test ora verificano quella risposta precisa.
- `kcadm get users` esclude gli account di servizio: fixture attesa Alice e Bob.
- Suite generale: aggiornati i due elenchi espliciti dei lab nei test,
  la tabella gruppi di UNATTENDED.md e la voce `services` di LAB_TRACKS
  in tools/tour/publish.py. Nessuna modifica al codice condiviso `vmctl/`.

Prossimo passo: completare il nuovo `make check` e la suite unittest completa,
committare provisioning e contenuti in passi separati, verificare nuovamente lo
stack dopo riavvio e registrare l'esito finale. TLS produzione solo spiegato:
il lab verificato resta intenzionalmente HTTP; nessuna prova di messa in produzione.

## Commit del provisioning

- Nuovo `make check`: PASS, mypy, 1564 pytest, 1762 subtest, 47 skip,
  dashboard 59 PASS. Resta il warning preesistente di `test_lab_traffic`.
- `group install` cumulativo ripetuto: PASS; `group test` dopo riavvio:
  **7/7 script, 15 check PASS**.
- Console Chromium: creati davvero realm `practice-console-check`, ruolo `reader`,
  utente `learner` e client pubblico `practice-web` con callback esatta;
  realm temporaneo poi eliminato. Le prove CLI restano nel test 02.
- Suite unittest completa ancora in corso; prima di chiudere va controllato il
  suo riepilogo (ha segnalato un fallimento da diagnosticare).

## Revisione del 2026-10-20 pomeriggio (Claude, dopo la fine del credito di Codex)

- Codex si è fermato per il limite d'uso mentre aggiornava `tests/test_repo_profiles.py` (la modifica era già scritta);
  tutto il resto era nel working tree, non committato. `python3 -m unittest discover -s tests`: 1611 test OK (47 skip).
- `vmctl group test keycloak-lab`: `test_07_errors.sh` rosso 2 volte su 3 su «expired token introspects as inactive».
  Misurato dal client: Keycloak risponde `active: true` fino a 0,9 s **dopo** `exp` (confronto a secondi interi sul suo
  orologio), e l'orologio del server stava mezzo secondo indietro (NTP sincronizzato su tutti e due). La prova aspettava
  1 s dopo `exp`: sul bordo. Ora aspetta 3 s dopo `exp` prima dell'introspection; 4/4 verdi da solo, poi la suite intera
  **7/7 script, 15 controlli**. Difetto della prova, non del lab.
- Guide lette: it 381 righe, en 374; sette esercizi in `lab.json`; `docs/LABS.md` con la riga del lab (2560 MiB).
