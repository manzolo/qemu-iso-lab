#!/usr/bin/env bash
# Shared by the mdadm-lab tests: the lab disks, a teardown that leaves them empty, a wait for the kernel.
LAB_DISKS=$(on mdadm-lab-server lsblk -dnbo NAME,SIZE,TYPE | awk '$2==2147483648 && $3=="disk" {printf "/dev/%s ", $1}')
LAB_DISKS=${LAB_DISKS% }
read -r DISK1 DISK2 DISK3 DISK4 <<<"$LAB_DISKS"


# Unmount, stop every array, zero the superblocks and wipe: the disks end as the install left them.
md_teardown() {
    on mdadm-lab-server "sudo umount -q /mnt/raid1 /mnt/raid5 2>/dev/null; for m in \$(ls /dev/md[0-9]* 2>/dev/null); do sudo mdadm --stop \$m >/dev/null 2>&1; done; for d in $LAB_DISKS; do sudo mdadm --zero-superblock \$d >/dev/null 2>&1; sudo wipefs -aq \$d; done; sudo rmdir /mnt/raid1 /mnt/raid5 2>/dev/null; true" >/dev/null 2>&1 || true
}

# Wait until /proc/mdstat shows no resync, recovery, reshape or check in progress (2 GiB disks: seconds).
md_settle() {
    for _ in $(seq 1 180); do
        on mdadm-lab-server cat /proc/mdstat 2>/dev/null | grep -qE "resync|recovery|reshape|check" || return 0
        sleep 2
    done
}
