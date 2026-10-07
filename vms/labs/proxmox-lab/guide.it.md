# Lab Proxmox VE: un cluster di tre nodi e un container che sopravvive al suo nodo

Tre nodi Proxmox VE 9.2, ciascuno su un mirror ZFS di due dischi, in cluster su un segmento isolato,
e un client Debian Xfce con un browser sullo stesso segmento. Il nodo 1 fa girare due container LXC,
IT-Tools e Glance. Gli esercizi leggono il cluster, replicano il disco di un container sugli altri
nodi, lo mettono in alta affidabilità e lo spostano a mano; poi al nodo 1 si stacca la corrente, e il
container riparte su un altro nodo, con lo stesso indirizzo, mentre il client continua a chiedere la
sua pagina.

| VM | Indirizzo nel lab | Dall'host |
|---|---|---|
| `proxmox-ve` (nodo 1, CT 200 IT-Tools, CT 201 Glance) | `10.10.10.2` | `vmctl shell proxmox-ve` (2276), GUI https://127.0.0.1:8006 |
| `proxmox-ve-node2` | `10.10.10.3` | `vmctl shell proxmox-ve-node2` (2278), GUI https://127.0.0.1:8007 |
| `proxmox-ve-node3` | `10.10.10.4` | `vmctl shell proxmox-ve-node3` (2279), GUI https://127.0.0.1:8008 |
| `proxmox-lab-client` | `10.10.10.10` | `vmctl shell proxmox-lab-client` (2277) o la sua console |

Il login della GUI è `root` / `lab`; i container rispondono sul segmento a http://10.10.10.20/
(IT-Tools) e http://10.10.10.21:8080/ (Glance). Ogni nodo vuole 3 GB di RAM: lo stack circa 12 GB.

## Avvio

```sh
vmctl group install proxmox-lab   # tre installazioni Proxmox automatiche, il client, poi il cluster: una quarantina di minuti
vmctl group status proxmox-lab
vmctl shell proxmox-ve
```

## Esercizio 1: il cluster

Corosync tiene i nodi in un solo cluster sul segmento del lab, e `/etc/pve` è lo stesso file system
su ogni nodo (pmxcfs). Un cluster agisce solo finché la maggioranza dei nodi, il *quorum*, si vede:
due su tre bastano. La GUI di un nodo qualsiasi gestisce tutti e tre.

```sh
pvecm status | sed -n '/^Name/p;/^Nodes/p;/^Quorate/p'
pvecm nodes
grep ring0_addr /etc/pve/corosync.conf
ls /etc/pve/nodes
```

## Esercizio 2: i container

Un container LXC condivide il kernel del nodo: parte in un secondo e chiede poche centinaia di MB.
Entrambi girano sul nodo 1, i loro dischi sono dataset ZFS su `local-zfs`, e ciascuno ha una seconda
interfaccia sul segmento del lab, da cui lo raggiunge il client.

```sh
pct list
pct config 200 | grep -E '^(hostname|memory|net1|rootfs)'
zfs list -o name,used,refer | grep subvol
pct exec 200 -- ip -4 -o addr show eth1
```

Sul client: `curl -s http://10.10.10.20/ | grep -o '<title>.*</title>'`. Sul nodo 2,
`zfs list | grep subvol` non trova niente: un container su storage locale esiste su un nodo solo.

## Esercizio 3: la replica ZFS

Un job di replica manda il dataset del container a un altro nodo con `zfs send`: intero la prima
volta, poi ogni minuto solo quello che è cambiato dall'ultimo snapshot. Una copia vecchia al massimo
di un minuto è ciò che permette a un altro nodo di avviare il container quando il suo nodo non c'è più.

```sh
pvesr create-local-job 200-0 proxmox-ve-node2 --schedule '*/1'
pvesr create-local-job 200-1 proxmox-ve-node3 --schedule '*/1'
pvesr schedule-now 200-0; pvesr schedule-now 200-1; sleep 15
pvesr status
```

Sul nodo 2: `zfs list -t all -o name,used | grep 200` mostra il dataset e il suo snapshot
`__replicate_`, una copia, non un container in esecuzione (lì `pct list` è vuoto).

## Esercizio 4: alta affidabilità, uno spostamento programmato

`ha-manager` si prende cura delle risorse che gli si affidano. Il cluster elegge un *master* CRM; l'LRM
di ogni nodo avvia e ferma ciò che gli viene detto e arma un watchdog. Spostare un container è una
migrazione con riavvio: si ferma sul nodo 1, le ultime modifiche vengono replicate, e riparte sul nodo
2 con la stessa configurazione e lo stesso indirizzo.

```sh
ha-manager add ct:200 --state started --max_relocate 1 --max_restart 1
sleep 20; ha-manager status
ha-manager migrate ct:200 proxmox-ve-node2
sleep 40; ha-manager status | grep ct:200
```

Il client riceve la stessa pagina da 10.10.10.20, servita ora dal nodo 2. Sul nodo 2, `pvesr status`
mostra che il job verso il nodo 2 ora punta al nodo 1: la replica segue il container. Riportalo a casa:

```sh
ha-manager migrate ct:200 proxmox-ve
```

## Esercizio 5: un nodo muore, il container si sposta

Sul client, lascia un ciclo che chiede la pagina ogni cinque secondi:

```sh
while true; do printf '%s ' $(date +%T); curl -s -m 2 http://10.10.10.20/ | grep -o '<title>IT Tools' || echo 'no answer'; sleep 5; done
```

Sull'host, stacca la corrente al nodo 1 (uno spegnimento brutale, non uno shutdown), e guarda dal nodo 2:

```sh
vmctl stop proxmox-ve --force
vmctl shell proxmox-ve-node2 -- "watch -n 3 'ha-manager status'"
```

I due nodi rimasti tengono il quorum. Dopo circa un minuto il master segna il servizio `fence`; quando
scade il lock del nodo morto (due minuti: a quel punto il suo watchdog lo avrebbe già riavviato, se
fosse stato solo isolato dagli altri) il container riparte sul nodo 2 o sul nodo 3 dall'ultima replica,
e il ciclo del client ritrova la pagina: circa due minuti e mezzo in tutto. Quello che è cambiato dopo
l'ultima sincronizzazione è perso: è il prezzo dello storage locale. Glance, che non è in HA, resta
spento finché il nodo 1 non torna.

```sh
vmctl group up proxmox-lab          # il nodo 1 torna e rientra nel cluster; il container resta dov'è
vmctl shell proxmox-ve-node2 -- pvesr status
vmctl shell proxmox-ve-node2 -- "pvesr schedule-now 200-0; sleep 15; pvesr status"
vmctl shell proxmox-ve-node2 -- ha-manager migrate ct:200 proxmox-ve
```

Il job verso il nodo 1 è fallito mentre il nodo 1 era spento, e un job fallito aspetta qualche minuto
prima di riprovare: lancialo a mano prima di riportare indietro il container. Una migrazione che trova
lì una copia vecchia copia invece tutto il disco con un nome nuovo (`subvol-200-disk-1`) e lascia
quella vecchia.

Da provare: `ha-manager crm-command node-maintenance enable proxmox-ve` sposta via le risorse prima
di una manutenzione programmata (`disable` restituisce il nodo). Per tornare al lab come installato:

```sh
ha-manager remove ct:200; for j in 200-0 200-1; do pvesr delete $j; done
```

## Test

```sh
vmctl group test proxmox-lab     # cinque script, uno per esercizio; il quinto spegne e riaccende il nodo 1 (circa 8 minuti)
```

Ogni test lascia il cluster come l'ha trovato: CT 200 sul nodo 1, fuori da HA, nessun job di replica.
