# LVM lab: physical volumes, groups, logical volumes and snapshots

One Ubuntu 24.04 server with three empty 2 GiB disks beside its system disk. They are there to be
used up and wiped: you turn them into physical volumes, pool them in a volume group, carve
logical volumes, grow one while it is mounted, take a snapshot and roll a change back, then tear it
all down. Ported from qlab's lvm-lab.

| VM | Disks | SSH from the host |
|---|---|---|
| `lvm-lab-server` | system 10 GiB + three empty 2 GiB lab disks | `vmctl shell lvm-lab-server` (127.0.0.1:2364) |

## Start

```bash
vmctl group install lvm-lab       # one cloud image plus three empty disks, about a minute after the download
vmctl shell lvm-lab-server
```

## Never type a disk name

The guest names virtio disks by their slot on the PCI bus, and here the three lab disks come
*first* (vda, vdb, vdc) and the system disk last (vdd). Rather than trusting either, pick the lab
disks by what they are — the 2G whole disks — and keep them in a variable for the session:

```bash
DISKS=$(lsblk -dnpo NAME,SIZE,TYPE | awk '$2=="2G" && $3=="disk" {print $1}' | xargs)
echo $DISKS
```

Every command below uses `$DISKS`. A real server deserves the same habit: `/dev/disk/by-id/` or a
size and a serial, never `/dev/sdX` from memory.

## Exercise 1: LVM anatomy

LVM has three layers: **physical volumes** (disks or partitions labelled for LVM), a **volume
group** that pools them, and **logical volumes** cut from the pool. The system disk is not LVM here.

```bash
lsblk                     # the 10G disk with partitions is the system; the three 2G ones are the lab's
sudo pvs; sudo vgs; sudo lvs
sudo wipefs -n $DISKS     # no signature: empty disks
```

## Exercise 2: physical volumes and a volume group

```bash
sudo pvcreate $DISKS
sudo pvs
sudo vgcreate labvg $DISKS
sudo vgs labvg            # about 6 GiB over 3 PVs
```

## Exercise 3: logical volumes with ext4 and xfs

A logical volume is a block device like a partition, but it can span disks and change size.

```bash
sudo lvcreate -L 1G -n data labvg && sudo mkfs.ext4 -F /dev/labvg/data
sudo lvcreate -L 1G -n logs labvg && sudo mkfs.xfs -f /dev/labvg/logs
sudo mkdir -p /mnt/lab-data /mnt/lab-logs
sudo mount /dev/labvg/data /mnt/lab-data
sudo mount /dev/labvg/logs /mnt/lab-logs
df -h /mnt/lab-data /mnt/lab-logs
sudo lvs labvg
```

## Exercise 4: grow a volume while it is mounted

`lvextend --resizefs` grows the logical volume and its file system in one step, online. ext4 and
xfs both grow online; only ext4 can shrink, and only unmounted.

```bash
echo before-growth | sudo tee /mnt/lab-data/keep
sudo lvextend -L +1G --resizefs labvg/data
df -h /mnt/lab-data && cat /mnt/lab-data/keep     # 2 GiB, the data still there
```

## Exercise 5: snapshot and rollback

A snapshot records the origin as it was; merging it back undoes every change made since. The
merge completes when the origin is not in use.

```bash
echo version-1 | sudo tee /mnt/lab-data/file
sudo lvcreate -s -L 256M -n data-snap labvg/data
echo version-2 | sudo tee /mnt/lab-data/file      # the change to undo
sudo umount /mnt/lab-data
sudo lvconvert --merge labvg/data-snap
sudo lvchange -an labvg/data && sudo lvchange -ay labvg/data
sudo mount /dev/labvg/data /mnt/lab-data
cat /mnt/lab-data/file                            # version-1 again
```

A snapshot is not a backup: it lives in the same volume group, on the same disks, and it fills up
as the origin changes (`sudo lvs` shows how much of its 256M is used).

## Exercise 6: tear it all down

```bash
sudo umount /mnt/lab-data /mnt/lab-logs
sudo vgremove -y labvg            # the logical volumes go with it
sudo pvremove $DISKS
sudo wipefs -a $DISKS
lsblk; sudo pvs                   # three empty disks again
```

## Tests

```bash
vmctl group test lvm-lab          # seven scripts; each builds what it needs and leaves the disks empty
```

The tests find the disks by size like the guide, and read LVM's numbers with `LC_ALL=C`: under an
Italian locale `vgs` prints `5,99g`, which no pattern written for `5.99g` would match.

## Stop and clean

```bash
vmctl group down lvm-lab
vmctl group clean lvm-lab         # deletes the system overlay and the three lab disks (asks first)
```
