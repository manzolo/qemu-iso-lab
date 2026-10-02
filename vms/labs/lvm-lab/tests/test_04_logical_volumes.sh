#!/usr/bin/env bash
# Exercise 4 — two logical volumes, ext4 and xfs, mounted and written; then removed.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_lvm.sh"
echo ""; echo "${BOLD}Exercise 4 — logical volumes and file systems${RESET}"; echo ""

lvm_teardown; trap lvm_teardown EXIT
lvm_vg
assert "a 1 GiB logical volume 'data'" on lvm-lab-server sudo lvcreate -qy -L 1G -n data labvg
assert "a 1 GiB logical volume 'logs'" on lvm-lab-server sudo lvcreate -qy -L 1G -n logs labvg
assert "ext4 on data" on lvm-lab-server sudo mkfs.ext4 -q /dev/labvg/data
assert "xfs on logs" on lvm-lab-server sudo mkfs.xfs -q /dev/labvg/logs
assert "both mounted" on lvm-lab-server "sudo mkdir -p /mnt/lab-data /mnt/lab-logs && sudo mount /dev/labvg/data /mnt/lab-data && sudo mount /dev/labvg/logs /mnt/lab-logs"
assert "a file written on each" on lvm-lab-server "echo one | sudo tee /mnt/lab-data/a /mnt/lab-logs/b >/dev/null"
# One findmnt per mount point: with two arguments findmnt reads them as source and target.
data=$(on lvm-lab-server findmnt -no FSTYPE /mnt/lab-data 2>/dev/null || true)
logs=$(on lvm-lab-server findmnt -no FSTYPE /mnt/lab-logs 2>/dev/null || true)
assert_contains "data is ext4" "$data" "^ext4$"
assert_contains "logs is xfs" "$logs" "^xfs$"

report_results "Exercise 4"
