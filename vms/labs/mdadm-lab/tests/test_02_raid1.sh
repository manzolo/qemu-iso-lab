#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mdadm.sh"
echo ""; echo "${BOLD}Exercise 2 — a RAID1 mirror${RESET}"; echo ""
md_teardown; trap md_teardown EXIT
assert "mdadm creates the mirror" on mdadm-lab-server sudo mdadm --create /dev/md0 --run --level=1 --raid-devices=2 "$DISK1" "$DISK2"
md_settle
detail=$(on mdadm-lab-server sudo mdadm --detail /dev/md0 2>/dev/null || true)
assert_contains "the level is raid1" "$detail" "Raid Level : raid1"
assert_contains "the array is clean" "$detail" "State : clean"
assert_contains "both members are up" "$(on mdadm-lab-server cat /proc/mdstat || true)" "\[UU\]"
assert "a file system mounts on it" on mdadm-lab-server "sudo mkfs.ext4 -q /dev/md0 && sudo mkdir -p /mnt/raid1 && sudo mount /dev/md0 /mnt/raid1 && echo mirrored | sudo tee /mnt/raid1/file >/dev/null"
assert_contains "the file reads back" "$(on mdadm-lab-server cat /mnt/raid1/file || true)" "^mirrored$"

report_results "Exercise 2"
