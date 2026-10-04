# Lab mdadm: il RAID software di Linux, un mirror, un disco guasto, uno spare e un RAID5 allargato

Un server con quattro dischi vuoti da 2 GiB accanto al disco di sistema. `mdadm` guida il driver md del
kernel, il RAID software che non ha bisogno di un controller: gli array sono dispositivi a blocchi
`/dev/mdN` fatti di dischi membri, e `/proc/mdstat` è il cruscotto. Gli esercizi costruiscono un mirror,
lo rompono e lo risanano, costruiscono un RAID5 con uno spare che si ricostruisce da solo, allargano il
RAID5 a caldo, e fermano, riassemblano e smontano gli array. Il RAID che il nome del raid-lab di qlab
prometteva.

| VM | Dischi | SSH dall'host |
|---|---|---|
| `mdadm-lab-server` | sistema 10 GiB + quattro dischi del lab vuoti da 2 GiB | `vmctl shell mdadm-lab-server` (127.0.0.1:2367) |

## Avvio

```sh
vmctl group install mdadm-lab    # una cloud image più quattro dischi vuoti, circa un minuto dopo il download
vmctl shell mdadm-lab-server
DISKS=$(lsblk -dnpo NAME,SIZE,TYPE | awk '$2=="2G" && $3=="disk" {print $1}' | xargs); echo $DISKS
```

I dischi del lab si scelgono per dimensione, mai per nome: il guest dà il nome ai dischi virtio in base
allo slot, i quattro del lab vengono prima e il disco di sistema per ultimo. Tieni `$DISKS` per tutta la
sessione; un server vero merita `/dev/disk/by-id/` per lo stesso motivo.

## Esercizio 1: anatomia del RAID

```sh
mdadm --version
cat /proc/mdstat
```

`Personalities` elenca i livelli RAID che il kernel sa gestire; nessun array ancora.

Ogni array di questo lab si crea con `--size=256M --run`. `--size` usa solo i primi 256 MiB di ogni
membro, così una sincronizzazione, una ricostruzione o un reshape durano pochi secondi anche su dischi
virtuali lenti; su un server vero si omette e l'array prende tutto il disco. `--run` risponde alla
domanda su cui `--create` altrimenti si ferma, "Continue creating array? (y/n)": mdadm la fa perché
gran parte di ogni disco resta inutilizzata e perché i metadati stanno all'inizio dei membri (va bene
per i dati, non per `/boot`).

## Esercizio 2: un mirror RAID1

Due dischi, ogni blocco scritto su entrambi: uno può guastarsi. `--create` avvia subito la prima
sincronizzazione (un disco copiato sull'altro: secondi qui, ore su dischi veri); `[UU]` in
`/proc/mdstat` dice che entrambi i membri sono su. L'array è un dispositivo a blocchi come un altro: si
formatta, si monta.

```sh
sudo mdadm --create /dev/md0 --size=256M --run --level=1 --raid-devices=2 $(echo $DISKS | cut -d' ' -f1) $(echo $DISKS | cut -d' ' -f2)
cat /proc/mdstat
sudo mdadm --detail /dev/md0
sudo mkfs.ext4 -F -q /dev/md0 && sudo mkdir -p /mnt/raid1 && sudo mount /dev/md0 /mnt/raid1 && echo mirrored | sudo tee /mnt/raid1/file
```

## Esercizio 3: un disco guasto, sostituito

`--fail` è quello che ti fa un disco morente: l'array diventa degradato, `[U_]`, e il file è ancora lì.
`--remove` toglie il membro, `--add` mette dentro un disco, e il kernel ricostruisce sopra di lui.

```sh
sudo mdadm /dev/md0 --fail $(echo $DISKS | cut -d' ' -f1) && cat /proc/mdstat
cat /mnt/raid1/file
sudo mdadm /dev/md0 --remove $(echo $DISKS | cut -d' ' -f1) && sudo mdadm /dev/md0 --add $(echo $DISKS | cut -d' ' -f1)
cat /proc/mdstat
```

## Esercizio 4: un RAID5 con uno spare a caldo

Il RAID5 distribuisce dati e parità su tre dischi: lo spazio di due, uno qualsiasi può guastarsi. Il
quarto disco, dichiarato spare, aspetta. Fai fallire un membro e la ricostruzione sullo spare parte da
sola: è tutto il senso di uno spare a caldo, nessuno deve essere sveglio. `--zero-superblock` cancella
solo i metadati di md: l'ext4 del mirror è ancora sui primi due dischi, quindi `mkfs.ext4` chiederebbe
"Proceed anyway?" e `-F` risponde sì.

```sh
sudo umount /mnt/raid1; sudo mdadm --stop /dev/md0; sudo mdadm --zero-superblock $(echo $DISKS | cut -d' ' -f1) $(echo $DISKS | cut -d' ' -f2)
sudo mdadm --create /dev/md1 --size=256M --run --level=5 --raid-devices=3 --spare-devices=1 $DISKS
cat /proc/mdstat; sudo mdadm --detail /dev/md1 | grep -E 'Raid Level|Array Size|State|spare'
sudo mkfs.ext4 -F -q /dev/md1 && sudo mkdir -p /mnt/raid5 && sudo mount /dev/md1 /mnt/raid5 && echo striped | sudo tee /mnt/raid5/file
sudo mdadm /dev/md1 --fail $(echo $DISKS | cut -d' ' -f2) && cat /proc/mdstat
sudo mdadm --detail /dev/md1 | grep -E 'State|Active|Working|Failed|Spare'
cat /mnt/raid5/file
```

## Esercizio 5: allargare il RAID5 a caldo

Rimetti il disco guasto come nuovo membro e rimodella l'array da tre dispositivi a quattro: i dati
vengono ridistribuiti su tutti mentre il file system resta montato. Poi `resize2fs` prende lo spazio
nuovo.

```sh
sudo mdadm /dev/md1 --remove $(echo $DISKS | cut -d' ' -f2) && sudo mdadm /dev/md1 --add $(echo $DISKS | cut -d' ' -f2)
sudo mdadm --grow /dev/md1 --raid-devices=4 && cat /proc/mdstat
sudo mdadm --detail /dev/md1 | grep -E 'Raid Devices|Array Size'
sudo resize2fs /dev/md1 && df -h /mnt/raid5
```

## Esercizio 6: scan, stop, assemble, smontaggio

`mdadm --detail --scan` stampa la riga `ARRAY` che `/etc/mdadm/mdadm.conf` vuole perché l'array torni
al boot (poi `update-initramfs -u`). Un array si può fermare e riassemblare dai superblocchi sui suoi
membri; azzerare quei superblocchi è ciò che lo chiude.

```sh
sudo mdadm --detail --scan
sudo umount /mnt/raid5 && sudo mdadm --stop /dev/md1 && cat /proc/mdstat
sudo mdadm --assemble --scan && cat /proc/mdstat && sudo mount /dev/md1 /mnt/raid5 && cat /mnt/raid5/file
sudo umount /mnt/raid5; sudo mdadm --stop /dev/md1
for d in $DISKS; do sudo mdadm --zero-superblock $d; sudo wipefs -a $d; done; cat /proc/mdstat; lsblk
```

## Test

```sh
vmctl group test mdadm-lab       # sei script, uno per esercizio, via SSH; i dischi finiscono vuoti
```
