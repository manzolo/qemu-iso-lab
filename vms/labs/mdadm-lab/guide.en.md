# mdadm lab: Linux software RAID, a mirror, a failed disk, a hot spare and a grown RAID5

One server with four empty 2 GiB disks beside its system disk. `mdadm` drives the kernel's md driver,
the software RAID that needs no controller: arrays are `/dev/mdN` block devices made of member disks,
and `/proc/mdstat` is the dashboard. The exercises build a mirror, break it and heal it, build a RAID5
with a hot spare that rebuilds by itself, grow the RAID5 online, and stop, assemble and tear the arrays
down. The RAID the name of qlab's raid-lab promised.

| VM | Disks | SSH from the host |
|---|---|---|
| `mdadm-lab-server` | a 10 GiB system disk + four empty 2 GiB lab disks | `vmctl shell mdadm-lab-server` (127.0.0.1:2367) |

## Start

```sh
vmctl group install mdadm-lab    # one cloud image plus four empty disks, about a minute after the download
vmctl shell mdadm-lab-server
DISKS=$(lsblk -dnpo NAME,SIZE,TYPE | awk '$2=="2G" && $3=="disk" {print $1}' | xargs); echo $DISKS
```

The lab disks are picked by size, never by name: the guest names virtio disks by their slot, the four
lab disks come first and the system disk last. Keep `$DISKS` for the whole session; a real server
deserves `/dev/disk/by-id/` for the same reason.

## Exercise 1: RAID anatomy

```sh
mdadm --version
cat /proc/mdstat
```

`Personalities` lists the RAID levels the kernel can run; no array yet.

Every array of this lab is created with `--size=256M --run`. `--size` uses only the first 256 MiB of
each member, so a sync, a rebuild or a reshape takes seconds even on slow virtual disks; on a real
server leave it out and the array takes the whole disk. `--run` answers the question `--create` would
otherwise stop on, "Continue creating array? (y/n)": mdadm asks because most of each disk stays unused
and because the metadata sits at the start of the members (fine for data, not for `/boot`).

## Exercise 2: a RAID1 mirror

Two disks, every block written to both: one may fail. `--create` starts the first sync at once (one
disk copied onto the other, seconds here, hours on real disks); `[UU]` in `/proc/mdstat` means both
members are up. The array is a block device like any other: format it, mount it.

```sh
sudo mdadm --create /dev/md0 --size=256M --run --level=1 --raid-devices=2 $(echo $DISKS | cut -d' ' -f1) $(echo $DISKS | cut -d' ' -f2)
cat /proc/mdstat
sudo mdadm --detail /dev/md0
sudo mkfs.ext4 -F -q /dev/md0 && sudo mkdir -p /mnt/raid1 && sudo mount /dev/md0 /mnt/raid1 && echo mirrored | sudo tee /mnt/raid1/file
```

## Exercise 3: a failed disk, replaced

`--fail` is what a dying disk does to you: the array goes degraded, `[U_]`, and the file is still
there. `--remove` takes the member out, `--add` puts a disk in, and the kernel rebuilds onto it.

```sh
sudo mdadm /dev/md0 --fail $(echo $DISKS | cut -d' ' -f1) && cat /proc/mdstat
cat /mnt/raid1/file
sudo mdadm /dev/md0 --remove $(echo $DISKS | cut -d' ' -f1) && sudo mdadm /dev/md0 --add $(echo $DISKS | cut -d' ' -f1)
cat /proc/mdstat
```

## Exercise 4: a RAID5 with a hot spare

RAID5 stripes data and parity across three disks: the space of two, any one may fail. The fourth disk,
declared as a spare, waits. Fail a member and the rebuild onto the spare starts by itself: that is the
whole point of a hot spare, nobody has to be awake. `--zero-superblock` erases only md's metadata:
the mirror's ext4 is still on the first two disks, so `mkfs.ext4` would ask "Proceed anyway?" and `-F`
says yes.

```sh
sudo umount /mnt/raid1; sudo mdadm --stop /dev/md0; sudo mdadm --zero-superblock $(echo $DISKS | cut -d' ' -f1) $(echo $DISKS | cut -d' ' -f2)
sudo mdadm --create /dev/md1 --size=256M --run --level=5 --raid-devices=3 --spare-devices=1 $DISKS
cat /proc/mdstat; sudo mdadm --detail /dev/md1 | grep -E 'Raid Level|Array Size|State|spare'
sudo mkfs.ext4 -F -q /dev/md1 && sudo mkdir -p /mnt/raid5 && sudo mount /dev/md1 /mnt/raid5 && echo striped | sudo tee /mnt/raid5/file
sudo mdadm /dev/md1 --fail $(echo $DISKS | cut -d' ' -f2) && cat /proc/mdstat
sudo mdadm --detail /dev/md1 | grep -E 'State|Active|Working|Failed|Spare'
cat /mnt/raid5/file
```

## Exercise 5: grow the RAID5 online

Put the failed disk back as a new member and reshape from three devices to four: the data is laid out
again across all of them while the file system stays mounted. Then `resize2fs` takes the new space.

```sh
sudo mdadm /dev/md1 --remove $(echo $DISKS | cut -d' ' -f2) && sudo mdadm /dev/md1 --add $(echo $DISKS | cut -d' ' -f2)
sudo mdadm --grow /dev/md1 --raid-devices=4 && cat /proc/mdstat
sudo mdadm --detail /dev/md1 | grep -E 'Raid Devices|Array Size'
sudo resize2fs /dev/md1 && df -h /mnt/raid5
```

## Exercise 6: scan, stop, assemble, tear down

`mdadm --detail --scan` prints the `ARRAY` line `/etc/mdadm/mdadm.conf` wants, so the array comes
back at boot (then `update-initramfs -u`). An array can be stopped and assembled again from the
superblocks on its members; zeroing those superblocks is what ends it.

```sh
sudo mdadm --detail --scan
sudo umount /mnt/raid5 && sudo mdadm --stop /dev/md1 && cat /proc/mdstat
sudo mdadm --assemble --scan && cat /proc/mdstat && sudo mount /dev/md1 /mnt/raid5 && cat /mnt/raid5/file
sudo umount /mnt/raid5; sudo mdadm --stop /dev/md1
for d in $DISKS; do sudo mdadm --zero-superblock $d; sudo wipefs -a $d; done; cat /proc/mdstat; lsblk
```

## Tests

```sh
vmctl group test mdadm-lab       # six scripts, one per exercise, over SSH; the disks end empty
```
