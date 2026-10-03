#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_zfs.sh"
echo ""; echo "${BOLD}Exercise 2 — a RAIDZ pool${RESET}"; echo ""
zfs_teardown; trap zfs_teardown EXIT
assert "zpool create raidz over the four disks" on zfs-lab-server sudo zpool create -f tank raidz $LAB_DISKS
status=$(on zfs-lab-server sudo zpool status tank 2>/dev/null || true)
assert_contains "the pool is a raidz1 vdev" "$status" "raidz1-0"
for d in $LAB_DISKS; do assert_contains "$d is in the pool" "$status" "$(basename $d)"; done
assert_contains "the pool is ONLINE" "$(on zfs-lab-server sudo zpool list -H -o health tank 2>/dev/null || true)" "^ONLINE$"
assert "the pool is mounted at /tank" on zfs-lab-server findmnt /tank

report_results "Exercise 2"
