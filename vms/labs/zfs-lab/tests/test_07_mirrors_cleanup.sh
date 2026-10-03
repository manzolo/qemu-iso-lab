#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_zfs.sh"
echo ""; echo "${BOLD}Exercise 7 — mirrors, then clean up${RESET}"; echo ""
zfs_teardown; trap zfs_teardown EXIT
zfs_pool
raidz_avail=$(on zfs-lab-server sudo zfs list -H -p -o avail tank 2>/dev/null || echo 0)
zfs_teardown
assert "two mirrored pairs over the four disks" on zfs-lab-server sudo zpool create -f tank mirror "$DISK1" "$DISK2" mirror "$DISK3" "$DISK4"
status=$(on zfs-lab-server sudo zpool status tank 2>/dev/null || true)
assert_contains "the pool has mirror-0" "$status" "mirror-0"
assert_contains "the pool has mirror-1" "$status" "mirror-1"
mirror_avail=$(on zfs-lab-server sudo zfs list -H -p -o avail tank 2>/dev/null || echo 0)
assert "mirrors offer less space than raidz over the same disks" test "$mirror_avail" -lt "$raidz_avail"
zfs_teardown
assert_fail "the pool is gone" on zfs-lab-server sudo zpool list tank
for d in $LAB_DISKS; do
    sig=$(on zfs-lab-server sudo wipefs -n "$d" 2>/dev/null || true)
    assert_not_contains "$d carries no ZFS label" "$sig" "zfs_member"
done

report_results "Exercise 7"
