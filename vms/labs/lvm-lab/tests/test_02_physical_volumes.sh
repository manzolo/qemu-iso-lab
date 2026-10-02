#!/usr/bin/env bash
# Exercise 2 — physical volumes on the three disks, then removed.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_lvm.sh"
echo ""; echo "${BOLD}Exercise 2 — physical volumes${RESET}"; echo ""

lvm_teardown; trap lvm_teardown EXIT
assert "pvcreate on the three disks" on lvm-lab-server sudo pvcreate -qy $LAB_DISKS
pvs=$(on lvm-lab-server sudo pvs --noheadings -o pv_name 2>/dev/null || true)
for d in $LAB_DISKS; do assert_contains "pvs lists $d" "$pvs" "$d"; done
sig=$(on lvm-lab-server sudo wipefs -n "$DISK1" 2>/dev/null || true)
assert_contains "the disk now carries an LVM2 label" "$sig" "LVM2_member"
assert "pvremove takes them back" on lvm-lab-server sudo pvremove -qy $LAB_DISKS

report_results "Exercise 2"
