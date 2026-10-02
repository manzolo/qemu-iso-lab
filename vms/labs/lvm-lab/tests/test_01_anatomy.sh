#!/usr/bin/env bash
# Exercise 1 — the tools and three empty disks. Read-only.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_lvm.sh"
echo ""; echo "${BOLD}Exercise 1 — LVM anatomy${RESET}"; echo ""

assert "the LVM tools are installed" on lvm-lab-server command -v pvcreate vgcreate lvcreate lvextend
assert "mkfs.ext4 and mkfs.xfs are installed" on lvm-lab-server command -v mkfs.ext4 mkfs.xfs
assert_contains "three 2 GiB lab disks are attached" "$(wc -w <<<"$LAB_DISKS")" "^3$"
for d in $LAB_DISKS; do
    sig=$(on lvm-lab-server sudo wipefs -n "$d" 2>/dev/null || true)
    assert_not_contains "$d carries no signature" "$sig" "LVM2|ext4|xfs|gpt|dos"
done
assert_fail "no labvg volume group exists yet" on lvm-lab-server sudo vgs labvg

report_results "Exercise 1"
