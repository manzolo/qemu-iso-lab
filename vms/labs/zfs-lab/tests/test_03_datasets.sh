#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_zfs.sh"
echo ""; echo "${BOLD}Exercise 3 — datasets and compression${RESET}"; echo ""
zfs_teardown; trap zfs_teardown EXIT
zfs_pool
assert_contains "tank/data is mounted at /tank/data" "$(on zfs-lab-server sudo zfs list -H -o mountpoint tank/data 2>/dev/null || true)" "^/tank/data$"
assert_contains "compression is lz4" "$(on zfs-lab-server sudo zfs get -H -o value compression tank/data 2>/dev/null || true)" "^lz4$"
on zfs-lab-server "yes 'the same line, again and again, compresses very well' | head -c 20M | sudo tee /tank/data/text.bin >/dev/null && sync" >/dev/null
ratio=$(on zfs-lab-server sudo zfs get -H -o value compressratio tank/data 2>/dev/null || true)
assert_contains "the text compressed well (ratio above 1.5x)" "$ratio" "^([2-9]|[1-9][0-9]+|1\.[5-9])"
assert_contains "the dataset uses less than the file's size" "$(on zfs-lab-server sudo zfs list -H -p -o used tank/data 2>/dev/null || true)" "^[0-9]{1,7}$"

report_results "Exercise 3"
