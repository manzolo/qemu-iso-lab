#!/usr/bin/env bash
# Exercise 7 — tear everything down: the three disks end empty, as the install left them.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_lvm.sh"
echo ""; echo "${BOLD}Exercise 7 — clean up${RESET}"; echo ""

lvm_vg
on lvm-lab-server "sudo lvcreate -qy -L 512M -n data labvg && sudo mkfs.ext4 -q /dev/labvg/data && sudo mkdir -p /mnt/lab-data && sudo mount /dev/labvg/data /mnt/lab-data" >/dev/null
lvm_teardown
assert_fail "no file system is mounted at /mnt/lab-data" on lvm-lab-server findmnt /mnt/lab-data
assert_fail "the volume group is gone" on lvm-lab-server sudo vgs labvg
pvs=$(on lvm-lab-server sudo pvs --noheadings -o pv_name 2>/dev/null || true)
for d in $LAB_DISKS; do
    assert_not_contains "$d is no longer a physical volume" "$pvs" "$d"
    sig=$(on lvm-lab-server sudo wipefs -n "$d" 2>/dev/null || true)
    assert_not_contains "$d carries no signature again" "$sig" "LVM2|ext4|xfs"
done

report_results "Exercise 7"
