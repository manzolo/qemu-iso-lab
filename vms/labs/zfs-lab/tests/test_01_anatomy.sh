#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_zfs.sh"
echo ""; echo "${BOLD}Exercise 1 — ZFS anatomy${RESET}"; echo ""
zfs_teardown; trap zfs_teardown EXIT
assert "the zfs kernel module is loaded" on zfs-lab-server "lsmod | grep -q '^zfs'"
assert_contains "zfs version answers" "$(on zfs-lab-server zfs version 2>&1 || true)" "zfs-"
assert "zpool and zfs are installed" on zfs-lab-server command -v zpool zfs
assert_contains "four 2 GiB lab disks are attached" "$(echo "$LAB_DISKS" | wc -w)" "^4$"
assert_fail "no pool exists yet" on zfs-lab-server sudo zpool list tank

report_results "Exercise 1"
