#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_zfs.sh"
echo ""; echo "${BOLD}Exercise 6 — send and receive${RESET}"; echo ""
zfs_teardown; trap zfs_teardown EXIT
zfs_pool
on zfs-lab-server "echo keep-me | sudo tee /tank/data/file >/dev/null && sudo zfs snapshot tank/data@backup" >/dev/null
assert "send | receive makes tank/copy" on zfs-lab-server "sudo zfs send tank/data@backup | sudo zfs receive tank/copy"
assert_contains "tank/copy is a dataset of the pool" "$(on zfs-lab-server sudo zfs list -H -o name 2>/dev/null || true)" "^tank/copy$"
assert_contains "the copy holds the file" "$(on zfs-lab-server cat /tank/copy/file 2>/dev/null || true)" "^keep-me$"
assert_contains "the copy carries the snapshot too" "$(on zfs-lab-server sudo zfs list -H -t snapshot -o name 2>/dev/null || true)" "^tank/copy@backup$"
assert "the copy can be destroyed with its snapshot" on zfs-lab-server sudo zfs destroy -r tank/copy

report_results "Exercise 6"
