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
