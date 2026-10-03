#!/usr/bin/env bash
# Shared by the zfs-lab tests: the lab disks and a teardown that leaves them empty.
# The four disks are found by size (2 GiB whole disks), never by name: the order is the virtio bus's.
LAB_DISKS=$(on zfs-lab-server lsblk -dnbo NAME,SIZE,TYPE | awk '$2==2147483648 && $3=="disk" {printf "/dev/%s ", $1}')
LAB_DISKS=${LAB_DISKS% }
read -r DISK1 DISK2 DISK3 DISK4 <<<"$LAB_DISKS"

# Destroy the pool and clear every ZFS label: the disks end as the install left them.
zfs_teardown() {
    on zfs-lab-server "sudo zpool destroy -f tank >/dev/null 2>&1; for d in $LAB_DISKS; do sudo zpool labelclear -f \$d >/dev/null 2>&1; sudo wipefs -aq \$d; done; true" >/dev/null 2>&1 || true
}

# The RAIDZ pool with the data dataset, for the tests that start from one.
zfs_pool() {
    on zfs-lab-server "sudo zpool create -f tank raidz $LAB_DISKS && sudo zfs create -o compression=lz4 tank/data" >/dev/null
}

# Wait until the pool reports no resilver or scrub in progress (a few seconds on 2 GiB disks).
zfs_settle() {
    for _ in $(seq 1 60); do
        on zfs-lab-server "sudo zpool status tank" 2>/dev/null | grep -qE "in progress|resilvering" || return 0
        sleep 1
    done
}
