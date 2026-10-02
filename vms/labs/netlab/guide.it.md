# Lab di rete: pfSense, Pi-hole e un client

Tre VM su una LAN isolata (`lab-lan`, 192.168.0.0/24, dominio `qlan`):

| VM | Ruolo | Indirizzo |
|---|---|---|
| `pfsense-lab` | router e firewall: WAN sulla NAT dell'host, LAN 192.168.0.1 | 192.168.0.1 |
| `pihole-lab` | DNS e DHCP della LAN, blocco della pubblicità | 192.168.0.10 |
| `lubuntu-lab` | il desktop dell'utente | 192.168.0.100 (lease fisso) |

L'host raggiunge il lab solo attraverso i forward sulla WAN del router, su 127.0.0.1: GUI di
pfSense `http://127.0.0.1:8080/`, Pi-hole `http://127.0.0.1:8081/admin/`, SSH `vmctl shell <vm>`.
Come si incastrano i pezzi, cosa fanno le installazioni e cosa è stato verificato dal vivo:
[docs/NETWORK-LAB.md](../../../docs/NETWORK-LAB.md).

## Avvio

```bash
vmctl group install netlab        # installa quello che manca (pfSense vuole la sua ISO, vedi il profilo), poi avvia lo stack
vmctl group status netlab
vmctl group map netlab --open     # la mappa, la tabella degli accessi e gli esercizi qui sotto
```

## Esercizi

Gli esercizi stanno in `lab.json` e nella pagina della mappa, ognuno come blocchi *check*
(sola lettura) o *try* (reversibili) con la macchina su cui girano:

1. **DNS e DHCP da Pi-hole** — chi risponde ai nomi del client, chi distribuisce i lease.
2. **Attraverso il firewall** — l'unica strada del client verso Internet è il router.

Un comando su una macchina senza aprire una sessione: `vmctl shell pihole-lab -- pihole status`.

## Test

```bash
bash vms/labs/netlab/tests/test_01_dns.sh
bash vms/labs/netlab/tests/test_02_firewall.sh
```

Ogni script carica `vms/labs/_common.sh` (`on <vm> <comando>`, `assert`, `assert_contains`,
`report_results`) e lascia il lab come l'ha trovato; l'exit status è il numero di check falliti.

## Spegnere e pulire

```bash
vmctl group down netlab
vmctl group clean netlab          # cancella i tre dischi (chiede prima); ISO e checkpoint restano
```
