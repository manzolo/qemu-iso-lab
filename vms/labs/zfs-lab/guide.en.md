# ZFS lab: a RAIDZ pool, datasets, snapshots, a failed disk and a scrub

One server with four empty 2 GiB disks beside its system disk, to be pooled, broken a little and wiped.
ZFS is the volume manager and the file system in one stack: a pool of disks, datasets mounted from it,
a checksum on every block. The exercises go from the first pool to the things that make ZFS worth it:
compression that costs nothing, snapshots and rollback, a disk failing and coming back, a scrub,
send and receive, and the choice between RAIDZ and mirrors. The ZFS half of qlab's raid-lab, extended.

| VM | Disks | SSH from the host |
|---|---|---|
| `zfs-lab-server` | a 10 GiB system disk + four empty 2 GiB lab disks | `vmctl shell zfs-lab-server` (127.0.0.1:2366) |

## Start

```sh
vmctl group install zfs-lab      # one cloud image plus four empty disks, about a minute after the download
vmctl shell zfs-lab-server
```

## Never write a disk's name by hand

The guest names virtio disks by their slot on the PCI bus, and here the lab disks come *first*
(`vda`..`vdd`) and the system disk last (`vde`). Pick the lab disks by what they are, whole 2 GiB
disks, and keep them in a variable for the whole session:

```sh
DISKS=$(lsblk -dnpo NAME,SIZE,TYPE | awk '$2=="2G" && $3=="disk" {print $1}' | xargs)
echo $DISKS
```

A real server deserves the same habit: `/dev/disk/by-id/` names, never `sdX`.

## Exercise 1: ZFS anatomy

The kernel module and the tools (`zpool` for pools, `zfs` for datasets) come with Ubuntu. Nothing is
pooled yet.

```sh
lsmod | grep '^zfs'; zfs version
sudo zpool list
```

## Exercise 2: a RAIDZ pool

`zpool create` takes whole disks and a layout. `raidz` (RAIDZ1) stripes the data across the four disks
with one disk's worth of parity: any single disk may fail. The pool appears mounted at `/tank` at once,
no partitions, no `mkfs`, no fstab.

```sh
sudo zpool create -f tank raidz $DISKS
sudo zpool status tank
sudo zpool list -o name,size,alloc,free,health tank
df -h /tank
```

## Exercise 3: datasets and compression

A dataset is a file system cut from the pool: it has its own properties and uses only what it holds,
with no size to decide in advance. `compression=lz4` is free in practice and often saves a lot; this
text compresses many times over.

```sh
sudo zfs create tank/data && sudo zfs set compression=lz4 tank/data
yes 'the same line, again and again, compresses very well' | head -c 50M | sudo tee /tank/data/text.bin >/dev/null
sudo zfs get -H -o value compressratio tank/data
sudo zfs list -o name,used,avail,refer,mountpoint
```

## Exercise 4: snapshots and rollback

A snapshot is a read-only view of a dataset at one instant; it costs nothing until the data diverges.
`zfs diff` shows what changed since it, `zfs rollback` puts the dataset back as it was.

```sh
echo version-1 | sudo tee /tank/data/file
sudo zfs snapshot tank/data@v1
echo version-2 | sudo tee /tank/data/file
sudo zfs list -t snapshot; sudo zfs diff tank/data@v1
sudo zfs rollback tank/data@v1 && cat /tank/data/file
```

## Exercise 5: a failed disk, a resilver, a scrub

Take a disk offline: the pool goes DEGRADED and keeps serving reads and writes. Bring it back and ZFS
*resilvers* it, copying only the blocks it missed. A scrub reads every block of the pool against its
checksum and repairs what it can from parity: the cheap insurance a cron job should run every month.

```sh
sudo zpool offline tank $(echo $DISKS | cut -d' ' -f1) && sudo zpool status tank
echo written-while-degraded | sudo tee /tank/data/degraded
sudo zpool online tank $(echo $DISKS | cut -d' ' -f1) && sleep 2 && sudo zpool status tank
sudo zpool scrub tank && sleep 3 && sudo zpool status tank
```

## Exercise 6: send and receive

`zfs send` turns a snapshot into a stream of bytes and `zfs receive` makes a dataset out of it, here in
the same pool, in real life on another pool or another machine through `ssh`. With `-i` the stream
carries only the changes since a previous snapshot: that is how ZFS backups and replication work.

```sh
sudo zfs snapshot tank/data@backup
sudo zfs send tank/data@backup | sudo zfs receive tank/copy
sudo zfs list; cat /tank/copy/file
sudo zfs destroy -r tank/copy
```

## Exercise 7: mirrors instead of RAIDZ, then tear it all down

Two mirrored pairs striped together give half the raw space, against three quarters for RAIDZ1 over
four disks; in exchange any disk of each pair may fail, a resilver copies one whole disk at full speed,
and random reads are faster. Compare `AVAIL`, then leave the disks empty.

```sh
sudo zpool destroy tank
sudo zpool create -f tank mirror $(echo $DISKS | cut -d' ' -f1,2) mirror $(echo $DISKS | cut -d' ' -f3,4)
sudo zpool status tank; sudo zfs list tank
sudo zpool destroy tank
for d in $DISKS; do sudo zpool labelclear -f $d; sudo wipefs -a $d; done
lsblk
```

## The ZFS course: one step further

The five episodes of the course (tour page, *Courses*) go a little beyond the exercises. Their extra
commands, on a pool rebuilt from empty disks (`set -- $DISKS` names them `$1`..`$4`):

```sh
set -- $DISKS
sudo zpool create -f tank raidz $1 $2 $3 && sudo zpool add -f tank spare $4   # 1: RAIDZ on three, a hot spare
sudo zpool list -o name,size,alloc,free tank; sudo zfs list tank             # raw space vs usable space
sudo zpool iostat -v tank; sudo zpool history tank

sudo zfs create tank/projects; sudo zfs create tank/projects/web; sudo zfs create tank/projects/db
sudo zfs set compression=lz4 tank/projects                                    # 2: inherited by web and db
sudo zfs get -r -o name,property,value,source compression tank/projects
sudo zfs set quota=200M tank/projects/web                                     # a ceiling ("Disk quota exceeded")
sudo zfs set reservation=500M tank/projects/db                                # space guaranteed, taken from the others
sudo zfs set recordsize=16K tank/projects/db                                  # small blocks for a database
sudo zfs set compression=off tank/projects/db; sudo zfs inherit compression tank/projects/db
sudo zfs set mountpoint=/srv/web tank/projects/web
```

A deleted file's space comes back a few seconds later: ZFS frees it in the background.

```sh
sudo zfs snapshot tank/projects/web@monday                                    # 3: snapshots
ls /srv/web/.zfs/snapshot/monday/                                             # browse it, copy one file back
sudo zfs rollback -r tank/projects/web@monday                                 # -r destroys the later snapshots
sudo zfs hold keep tank/projects/web@monday; sudo zfs destroy tank/projects/web@monday   # refused: dataset is busy
sudo zfs release keep tank/projects/web@monday
sudo zfs clone tank/projects/web@monday tank/web-test; sudo zfs list -o name,origin -r tank
sudo zfs destroy tank/web-test

sudo dd if=/dev/urandom of=$2 bs=1M seek=300 count=600 conv=notrunc status=none   # 4: silent corruption, lab disks only
sudo zpool scrub -w tank; sudo zpool status tank                              # CKSUM counted and repaired
sudo zpool clear tank; grep scrub /etc/cron.d/zfsutils-linux                   # Ubuntu scrubs every month
sudo zpool replace tank $2 $4; sudo zpool detach tank $2                       # the spare takes the disk's place

sudo zfs send -nv -i @s1 tank/web@s2                                          # 5: estimate an incremental stream
sudo zpool export tank; sudo zpool import; sudo zpool import tank archive     # move a pool, rename it on import
```

## Tests

```sh
vmctl group test zfs-lab        # seven scripts, one per exercise, over SSH; the disks end empty
```
