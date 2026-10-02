#!/usr/bin/env bash
# Shared by the lvm-lab tests: the lab disks and a teardown that leaves them empty.
# The three disks are found by size (2 GiB whole disks), never by name: vdb..vdd today, but the
# order is the virtio bus's, not a promise.
LAB_DISKS=$(on lvm-lab-server lsblk -dnbo NAME,SIZE,TYPE | awk '$2==2147483648 && $3=="disk" {printf "/dev/%s ", $1}')
LAB_DISKS=${LAB_DISKS% }
read -r DISK1 DISK2 DISK3 <<<"$LAB_DISKS"

# Unmount, remove the volume group and the physical volumes, wipe the signatures: the disks end as
# the install left them. Every test calls it first and on exit.
lvm_teardown() {
    on lvm-lab-server "sudo umount -q /mnt/lab-data /mnt/lab-logs 2>/dev/null; sudo vgremove -fy labvg >/dev/null 2>&1; for d in $LAB_DISKS; do sudo pvremove -fy \$d >/dev/null 2>&1; sudo wipefs -aq \$d; done; sudo rmdir /mnt/lab-data /mnt/lab-logs 2>/dev/null; true" >/dev/null 2>&1 || true
}

# A volume group over the three disks, for the tests that start from one.
lvm_vg() {
    on lvm-lab-server "sudo pvcreate -qy $LAB_DISKS && sudo vgcreate -qy labvg $LAB_DISKS" >/dev/null
}
