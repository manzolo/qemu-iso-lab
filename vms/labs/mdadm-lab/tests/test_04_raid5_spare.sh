#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mdadm.sh"
echo ""; echo "${BOLD}Exercise 4 — a RAID5 with a hot spare${RESET}"; echo ""
md_teardown; trap md_teardown EXIT
assert "mdadm creates the RAID5 with a spare" on mdadm-lab-server sudo mdadm --create /dev/md1 --run --level=5 --raid-devices=3 --spare-devices=1 "$DISK1" "$DISK2" "$DISK3" "$DISK4"
md_settle
detail=$(on mdadm-lab-server sudo mdadm --detail /dev/md1 2>/dev/null || true)
assert_contains "the level is raid5" "$detail" "Raid Level : raid5"
assert_contains "one spare device" "$detail" "Spare Devices : 1"
size=$(on mdadm-lab-server lsblk -bdno SIZE /dev/md1 2>/dev/null || echo 0)
assert "the array holds the space of two disks" test "$size" -gt 4000000000
on mdadm-lab-server "sudo mkfs.ext4 -q /dev/md1 && sudo mkdir -p /mnt/raid5 && sudo mount /dev/md1 /mnt/raid5 && echo striped | sudo tee /mnt/raid5/file >/dev/null" >/dev/null
assert "a member fails" on mdadm-lab-server sudo mdadm /dev/md1 --fail "$DISK2"
sleep 2
md_settle
detail=$(on mdadm-lab-server sudo mdadm --detail /dev/md1 2>/dev/null || true)
assert_contains "the spare took over: three active devices" "$detail" "Active Devices : 3"
assert_contains "the failed member is listed faulty" "$detail" "Failed Devices : 1"
assert_contains "no spare is left waiting" "$detail" "Spare Devices : 0"
assert_contains "the file is intact" "$(on mdadm-lab-server cat /mnt/raid5/file || true)" "^striped$"

report_results "Exercise 4"
