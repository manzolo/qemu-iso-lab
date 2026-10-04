# Lab LVM: volumi fisici, gruppi, volumi logici e snapshot

Un server Ubuntu 24.04 con tre dischi vuoti da 2 GiB accanto al disco di sistema. Sono lì per
essere consumati e cancellati: li trasformi in volumi fisici, li riunisci in un volume group,
ricavi dei volumi logici, ne allarghi uno mentre è montato, fai uno snapshot e annulli una
modifica, poi smonti tutto. Portato dal lvm-lab di qlab.

| VM | Dischi | SSH dall'host |
|---|---|---|
| `lvm-lab-server` | sistema 10 GiB + tre dischi del lab vuoti da 2 GiB | `vmctl shell lvm-lab-server` (127.0.0.1:2364) |

## Avvio

```bash
vmctl group install lvm-lab       # un'immagine cloud più tre dischi vuoti, circa un minuto dopo il download
vmctl shell lvm-lab-server
```

## Mai scrivere a mano il nome di un disco

Il guest dà il nome ai dischi virtio in base allo slot sul bus PCI, e qui i tre dischi del lab
vengono *prima* (vda, vdb, vdc) e il disco di sistema per ultimo (vdd). Invece di fidarti
dell'uno o dell'altro, scegli i dischi del lab per quello che sono — i dischi interi da 2G — e
tienili in una variabile per tutta la sessione:

```bash
DISKS=$(lsblk -dnpo NAME,SIZE,TYPE | awk '$2=="2G" && $3=="disk" {print $1}' | xargs)
echo $DISKS
```

Ogni comando qui sotto usa `$DISKS`. Un server vero merita la stessa abitudine: `/dev/disk/by-id/`
o dimensione e seriale, mai `/dev/sdX` a memoria.

## Esercizio 1: anatomia di LVM

LVM ha tre livelli: i **volumi fisici** (dischi o partizioni etichettati per LVM), un **volume
group** che li riunisce e i **volumi logici** ricavati dal gruppo. Qui il disco di sistema non è LVM.

```bash
lsblk                     # il disco da 10G con le partizioni è il sistema; i tre da 2G sono del lab
sudo pvs; sudo vgs; sudo lvs
sudo wipefs -n $DISKS     # nessuna firma: dischi vuoti
```

## Esercizio 2: volumi fisici e un volume group

```bash
sudo pvcreate $DISKS
sudo pvs
sudo vgcreate labvg $DISKS
sudo vgs labvg            # circa 6 GiB su 3 PV
```

## Esercizio 3: volumi logici con ext4 e xfs

Un volume logico è un dispositivo a blocchi come una partizione, ma può stare su più dischi e
cambiare dimensione.

```bash
sudo lvcreate -L 1G -n data labvg && sudo mkfs.ext4 -F /dev/labvg/data
sudo lvcreate -L 1G -n logs labvg && sudo mkfs.xfs -f /dev/labvg/logs
sudo mkdir -p /mnt/lab-data /mnt/lab-logs
sudo mount /dev/labvg/data /mnt/lab-data
sudo mount /dev/labvg/logs /mnt/lab-logs
df -h /mnt/lab-data /mnt/lab-logs
sudo lvs labvg
```

## Esercizio 4: allargare un volume montato

`lvextend --resizefs` allarga il volume logico e il suo file system in un colpo solo, a caldo. ext4
e xfs crescono entrambi a caldo; solo ext4 si può restringere, e solo smontato.

```bash
echo before-growth | sudo tee /mnt/lab-data/keep
sudo lvextend -L +1G --resizefs labvg/data
df -h /mnt/lab-data && cat /mnt/lab-data/keep     # 2 GiB, i dati ancora lì
```

## Esercizio 5: snapshot e rollback

Uno snapshot fissa l'origine com'era; riunirlo all'origine annulla tutte le modifiche fatte da
allora. Il merge si completa quando l'origine non è in uso.

```bash
echo version-1 | sudo tee /mnt/lab-data/file
sudo lvcreate -s -L 256M -n data-snap labvg/data
echo version-2 | sudo tee /mnt/lab-data/file      # la modifica da annullare
sudo umount /mnt/lab-data
sudo lvconvert --merge labvg/data-snap
sudo lvchange -an labvg/data && sudo lvchange -ay labvg/data
sudo mount /dev/labvg/data /mnt/lab-data
cat /mnt/lab-data/file                            # di nuovo version-1
```

Uno snapshot non è un backup: vive nello stesso volume group, sugli stessi dischi, e si riempie man
mano che l'origine cambia (`sudo lvs` mostra quanto dei suoi 256M è occupato).

## Esercizio 6: smontare tutto

```bash
sudo umount /mnt/lab-data /mnt/lab-logs
sudo vgremove -y labvg            # i volumi logici se ne vanno con lui
sudo pvremove $DISKS
sudo wipefs -a $DISKS
lsblk; sudo pvs                   # di nuovo tre dischi vuoti
```

## Test

```bash
vmctl group test lvm-lab          # sette script; ognuno costruisce quello che gli serve e lascia i dischi vuoti
```

I test trovano i dischi per dimensione come la guida, e leggono i numeri di LVM con `LC_ALL=C`:
con il locale italiano `vgs` stampa `5,99g`, che nessun pattern scritto per `5.99g` riconoscerebbe.

## Spegnere e pulire

```bash
vmctl group down lvm-lab
vmctl group clean lvm-lab         # cancella l'overlay di sistema e i tre dischi del lab (chiede prima)
```
