#!/usr/bin/env bash
# Exercise 3 — one volume group over the three physical volumes, then removed.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_lvm.sh"
echo ""; echo "${BOLD}Exercise 3 — a volume group${RESET}"; echo ""

lvm_teardown; trap lvm_teardown EXIT
on lvm-lab-server sudo pvcreate -qy $LAB_DISKS >/dev/null
assert "vgcreate labvg over the three" on lvm-lab-server sudo vgcreate -qy labvg $LAB_DISKS
# LC_ALL=C: LVM prints numbers in the guest's locale (5,99g under it_IT, the identity of local.json).
size=$(on lvm-lab-server sudo env LC_ALL=C vgs --noheadings --units g -o vg_size labvg 2>/dev/null || true)
assert_contains "the group pools the three disks (about 6 GiB)" "$size" "5\.9[0-9]g"
count=$(on lvm-lab-server sudo vgs --noheadings -o pv_count labvg 2>/dev/null | tr -d ' ' || true)
assert_contains "it is made of 3 physical volumes" "$count" "^3$"
assert "vgremove removes it" on lvm-lab-server sudo vgremove -fy labvg

report_results "Exercise 3"
