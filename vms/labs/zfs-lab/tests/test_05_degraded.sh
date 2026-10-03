#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_zfs.sh"
echo ""; echo "${BOLD}Exercise 5 — a failed disk, a resilver, a scrub${RESET}"; echo ""
zfs_teardown; trap zfs_teardown EXIT
zfs_pool
on zfs-lab-server "echo before | sudo tee /tank/data/file >/dev/null" >/dev/null
assert "one disk goes offline" on zfs-lab-server sudo zpool offline tank "$DISK1"
assert_contains "the pool is DEGRADED and still serving" "$(on zfs-lab-server sudo zpool list -H -o health tank 2>/dev/null || true)" "^DEGRADED$"
assert "a write succeeds while degraded" on zfs-lab-server "echo written-while-degraded | sudo tee /tank/data/degraded >/dev/null"
assert "the disk comes back online" on zfs-lab-server sudo zpool online tank "$DISK1"
zfs_settle
assert_contains "the pool is ONLINE after the resilver" "$(on zfs-lab-server sudo zpool list -H -o health tank 2>/dev/null || true)" "^ONLINE$"
assert_contains "the status records the resilver" "$(on zfs-lab-server sudo zpool status tank 2>/dev/null || true)" "resilvered"
assert "a scrub starts" on zfs-lab-server sudo zpool scrub tank
zfs_settle
status=$(on zfs-lab-server sudo zpool status tank 2>/dev/null || true)
assert_contains "the scrub repaired nothing" "$status" "scrub repaired 0B"
assert_contains "no known data errors" "$status" "No known data errors"
assert_contains "the data written while degraded is intact" "$(on zfs-lab-server cat /tank/data/degraded 2>/dev/null || true)" "^written-while-degraded$"

report_results "Exercise 5"
