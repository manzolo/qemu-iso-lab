# Lab ZFS: un pool RAIDZ, dataset, snapshot, un disco guasto e uno scrub

Un server con quattro dischi vuoti da 2 GiB accanto al disco di sistema, da mettere in pool, rompere un
po' e ripulire. ZFS è volume manager e file system in un solo strato: un pool di dischi, dataset montati
da lì, un checksum su ogni blocco. Gli esercizi vanno dal primo pool alle cose per cui ZFS vale la pena:
compressione che non costa nulla, snapshot e rollback, un disco che si guasta e torna, uno scrub, send e
receive, e la scelta fra RAIDZ e mirror. La metà ZFS del raid-lab di qlab, ampliata.

| VM | Dischi | SSH dall'host |
|---|---|---|
| `zfs-lab-server` | sistema 10 GiB + quattro dischi del lab vuoti da 2 GiB | `vmctl shell zfs-lab-server` (127.0.0.1:2366) |

## Avvio

```sh
vmctl group install zfs-lab      # una cloud image più quattro dischi vuoti, circa un minuto dopo il download
vmctl shell zfs-lab-server
```

## Mai scrivere a mano il nome di un disco

Il guest dà il nome ai dischi virtio in base allo slot sul bus PCI, e qui i dischi del lab vengono
*prima* (`vda`..`vdd`) e il disco di sistema per ultimo (`vde`). Scegli i dischi del lab per quello che
sono, dischi interi da 2 GiB, e tienili in una variabile per tutta la sessione:

```sh
DISKS=$(lsblk -dnpo NAME,SIZE,TYPE | awk '$2=="2G" && $3=="disk" {print $1}' | xargs)
echo $DISKS
```

Un server vero merita la stessa abitudine: i nomi in `/dev/disk/by-id/`, mai `sdX`.

## Esercizio 1: anatomia di ZFS

Il modulo del kernel e gli strumenti (`zpool` per i pool, `zfs` per i dataset) arrivano con Ubuntu.
Ancora niente in pool.

```sh
lsmod | grep '^zfs'; zfs version
sudo zpool list
```

## Esercizio 2: un pool RAIDZ

`zpool create` prende dischi interi e una disposizione. `raidz` (RAIDZ1) distribuisce i dati sui quattro
dischi con l'equivalente di un disco di parità: può guastarsi un disco qualsiasi. Il pool compare subito
montato in `/tank`: niente partizioni, niente `mkfs`, niente fstab.

```sh
sudo zpool create -f tank raidz $DISKS
sudo zpool status tank
sudo zpool list -o name,size,alloc,free,health tank
df -h /tank
```

## Esercizio 3: dataset e compressione

Un dataset è un file system ritagliato dal pool: ha le sue proprietà e occupa solo quello che contiene,
senza una dimensione da decidere prima. `compression=lz4` in pratica è gratis e spesso fa risparmiare
molto; questo testo si comprime molte volte.

```sh
sudo zfs create tank/data && sudo zfs set compression=lz4 tank/data
yes 'the same line, again and again, compresses very well' | head -c 50M | sudo tee /tank/data/text.bin >/dev/null
sudo zfs get -H -o value compressratio tank/data
sudo zfs list -o name,used,avail,refer,mountpoint
```

## Esercizio 4: snapshot e rollback

Uno snapshot è una vista in sola lettura di un dataset in un istante; non costa niente finché i dati
non divergono. `zfs diff` mostra cosa è cambiato da allora, `zfs rollback` rimette il dataset com'era.

```sh
echo version-1 | sudo tee /tank/data/file
sudo zfs snapshot tank/data@v1
echo version-2 | sudo tee /tank/data/file
sudo zfs list -t snapshot; sudo zfs diff tank/data@v1
sudo zfs rollback tank/data@v1 && cat /tank/data/file
```

## Esercizio 5: un disco guasto, un resilver, uno scrub

Metti un disco offline: il pool passa a DEGRADED e continua a servire letture e scritture. Rimettilo e
ZFS lo *risincronizza* (resilver), copiando solo i blocchi che gli mancano. Uno scrub legge ogni blocco
del pool confrontandolo con il suo checksum e ripara quello che può dalla parità: l'assicurazione a
basso costo che un cron dovrebbe lanciare ogni mese.

```sh
sudo zpool offline tank $(echo $DISKS | cut -d' ' -f1) && sudo zpool status tank
echo written-while-degraded | sudo tee /tank/data/degraded
sudo zpool online tank $(echo $DISKS | cut -d' ' -f1) && sleep 2 && sudo zpool status tank
sudo zpool scrub tank && sleep 3 && sudo zpool status tank
```

## Esercizio 6: send e receive

`zfs send` trasforma uno snapshot in un flusso di byte e `zfs receive` ne ricava un dataset, qui nello
stesso pool, nella realtà su un altro pool o un'altra macchina attraverso `ssh`. Con `-i` il flusso
porta solo le differenze da uno snapshot precedente: così funzionano i backup e la replica di ZFS.

```sh
sudo zfs snapshot tank/data@backup
sudo zfs send tank/data@backup | sudo zfs receive tank/copy
sudo zfs list; cat /tank/copy/file
sudo zfs destroy -r tank/copy
```

## Esercizio 7: mirror al posto di RAIDZ, poi si smonta tutto

Due coppie in mirror messe in striping danno metà dello spazio grezzo, contro tre quarti del RAIDZ1 su
quattro dischi; in cambio può guastarsi un disco per coppia, un resilver copia un disco intero a piena
velocità e le letture casuali sono più veloci. Confronta `AVAIL`, poi lascia i dischi vuoti.

```sh
sudo zpool destroy tank
sudo zpool create -f tank mirror $(echo $DISKS | cut -d' ' -f1,2) mirror $(echo $DISKS | cut -d' ' -f3,4)
sudo zpool status tank; sudo zfs list tank
sudo zpool destroy tank
for d in $DISKS; do sudo zpool labelclear -f $d; sudo wipefs -a $d; done
lsblk
```

## Test

```sh
vmctl group test zfs-lab        # sette script, uno per esercizio, via SSH; i dischi finiscono vuoti
```
