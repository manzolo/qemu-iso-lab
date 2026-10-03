#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_zfs.sh"
echo ""; echo "${BOLD}Exercise 4 — snapshots and rollback${RESET}"; echo ""
zfs_teardown; trap zfs_teardown EXIT
zfs_pool
on zfs-lab-server "echo version-1 | sudo tee /tank/data/file >/dev/null && sudo zfs snapshot tank/data@v1 && echo version-2 | sudo tee /tank/data/file >/dev/null" >/dev/null
assert_contains "the snapshot is listed" "$(on zfs-lab-server sudo zfs list -H -t snapshot -o name 2>/dev/null || true)" "^tank/data@v1$"
assert_contains "zfs diff shows the modified file" "$(on zfs-lab-server sudo zfs diff tank/data@v1 2>/dev/null || true)" "M.*/tank/data/file"
assert "rollback to the snapshot" on zfs-lab-server sudo zfs rollback tank/data@v1
assert_contains "the file is version-1 again" "$(on zfs-lab-server cat /tank/data/file 2>/dev/null || true)" "^version-1$"
assert "the snapshot can be destroyed" on zfs-lab-server sudo zfs destroy tank/data@v1

report_results "Exercise 4"
