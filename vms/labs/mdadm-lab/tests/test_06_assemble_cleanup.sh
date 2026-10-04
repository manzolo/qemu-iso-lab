#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mdadm.sh"
echo ""; echo "${BOLD}Exercise 6 — scan, stop, assemble, tear down${RESET}"; echo ""
md_teardown; trap md_teardown EXIT
on mdadm-lab-server "sudo mdadm --create /dev/md1 --size=256M --run --level=5 --raid-devices=3 $DISK1 $DISK2 $DISK3" >/dev/null 2>&1
md_settle
on mdadm-lab-server "sudo mkfs.ext4 -F -q /dev/md1 && sudo mkdir -p /mnt/raid5 && sudo mount /dev/md1 /mnt/raid5 && echo striped | sudo tee /mnt/raid5/file >/dev/null" >/dev/null
assert_contains "--detail --scan prints the ARRAY line for mdadm.conf" "$(on mdadm-lab-server sudo mdadm --detail --scan 2>/dev/null || true)" "^ARRAY /dev/md"
assert "the array stops" on mdadm-lab-server "sudo umount /mnt/raid5 && sudo mdadm --stop /dev/md1"
assert_not_contains "no array is running" "$(on mdadm-lab-server cat /proc/mdstat || true)" "^md[0-9]"
assert "the array assembles again from its superblocks" on mdadm-lab-server sudo mdadm --assemble --scan
md_settle
dev=$(on mdadm-lab-server "sudo mdadm --detail --scan | awk '{print \$2}' | head -1" 2>/dev/null || true)
assert_contains "the file survives stop and assemble" "$(on mdadm-lab-server "sudo mount $dev /mnt/raid5 && cat /mnt/raid5/file" 2>/dev/null || true)" "^striped$"
md_teardown
assert_not_contains "every array is gone" "$(on mdadm-lab-server cat /proc/mdstat || true)" "^md[0-9]"
for d in $LAB_DISKS; do
    sig=$(on mdadm-lab-server sudo wipefs -n "$d" 2>/dev/null || true)
    assert_not_contains "$d carries no RAID superblock" "$sig" "linux_raid_member"
done

report_results "Exercise 6"
