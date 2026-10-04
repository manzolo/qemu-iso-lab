#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mdadm.sh"
echo ""; echo "${BOLD}Exercise 5 — grow the RAID5 online${RESET}"; echo ""
md_teardown; trap md_teardown EXIT
on mdadm-lab-server "sudo mdadm --create /dev/md1 --size=256M --run --level=5 --raid-devices=3 $DISK1 $DISK2 $DISK3" >/dev/null 2>&1
md_settle
on mdadm-lab-server "sudo mkfs.ext4 -q /dev/md1 && sudo mkdir -p /mnt/raid5 && sudo mount /dev/md1 /mnt/raid5 && echo striped | sudo tee /mnt/raid5/file >/dev/null" >/dev/null
before=$(on mdadm-lab-server lsblk -bdno SIZE /dev/md1 2>/dev/null || echo 0)
assert "a fourth disk is added" on mdadm-lab-server sudo mdadm /dev/md1 --add "$DISK4"
assert "the array grows to four devices" on mdadm-lab-server sudo mdadm --grow /dev/md1 --raid-devices=4
md_settle
after=$(on mdadm-lab-server lsblk -bdno SIZE /dev/md1 2>/dev/null || echo 0)
assert "the array is bigger after the reshape" test "$after" -gt "$before"
assert "resize2fs takes the new space while mounted" on mdadm-lab-server sudo resize2fs /dev/md1
assert_contains "the file is still there" "$(on mdadm-lab-server cat /mnt/raid5/file || true)" "^striped$"
assert_contains "mdadm reports four raid devices" "$(on mdadm-lab-server sudo mdadm --detail /dev/md1 2>/dev/null || true)" "Raid Devices : 4"

report_results "Exercise 5"
