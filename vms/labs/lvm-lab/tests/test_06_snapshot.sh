#!/usr/bin/env bash
# Exercise 6 — a snapshot, a change, and the rollback that undoes it; then removed.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_lvm.sh"
echo ""; echo "${BOLD}Exercise 6 — snapshot and rollback${RESET}"; echo ""

lvm_teardown; trap lvm_teardown EXIT
lvm_vg
on lvm-lab-server "sudo lvcreate -qy -L 1G -n data labvg && sudo mkfs.ext4 -q /dev/labvg/data && sudo mkdir -p /mnt/lab-data && sudo mount /dev/labvg/data /mnt/lab-data && echo version-1 | sudo tee /mnt/lab-data/file >/dev/null && sync" >/dev/null
assert "a 256 MiB snapshot of data" on lvm-lab-server sudo lvcreate -qy -s -L 256M -n data-snap labvg/data
on lvm-lab-server "echo version-2 | sudo tee /mnt/lab-data/file >/dev/null && sync" >/dev/null
now=$(on lvm-lab-server cat /mnt/lab-data/file)
assert_contains "the origin changed after the snapshot" "$now" "^version-2$"
attr=$(on lvm-lab-server sudo lvs --noheadings -o lv_attr labvg/data-snap 2>/dev/null | tr -d ' ' || true)
assert_contains "data-snap is a snapshot volume" "$attr" "^s"
# Roll back: unmount, merge the snapshot into the origin, reactivate so the merge completes.
assert "the snapshot merges back into the origin" on lvm-lab-server "sudo umount /mnt/lab-data && sudo lvconvert -q --merge labvg/data-snap && sudo lvchange -an labvg/data && sudo lvchange -ay labvg/data"
on lvm-lab-server "for i in \$(seq 1 20); do sudo lvs labvg/data-snap >/dev/null 2>&1 || break; sleep 1; done; sudo mount /dev/labvg/data /mnt/lab-data" >/dev/null
back=$(on lvm-lab-server cat /mnt/lab-data/file 2>/dev/null || true)
assert_contains "the rollback restored version 1" "$back" "^version-1$"
assert_fail "the snapshot is consumed by the merge" on lvm-lab-server sudo lvs labvg/data-snap

report_results "Exercise 6"
