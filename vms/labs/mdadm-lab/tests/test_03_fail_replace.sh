#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mdadm.sh"
echo ""; echo "${BOLD}Exercise 3 — a failed disk, replaced${RESET}"; echo ""
md_teardown; trap md_teardown EXIT
on mdadm-lab-server "sudo mdadm --create /dev/md0 --run --level=1 --raid-devices=2 $DISK1 $DISK2" >/dev/null 2>&1
md_settle
on mdadm-lab-server "sudo mkfs.ext4 -q /dev/md0 && sudo mkdir -p /mnt/raid1 && sudo mount /dev/md0 /mnt/raid1 && echo mirrored | sudo tee /mnt/raid1/file >/dev/null" >/dev/null
assert "one member is marked faulty" on mdadm-lab-server sudo mdadm /dev/md0 --fail "$DISK1"
assert_contains "the array is degraded" "$(on mdadm-lab-server cat /proc/mdstat || true)" "\[U_\]|\[_U\]"
assert_contains "the file is still readable" "$(on mdadm-lab-server cat /mnt/raid1/file || true)" "^mirrored$"
assert "the faulty member is removed" on mdadm-lab-server sudo mdadm /dev/md0 --remove "$DISK1"
assert "a disk is added back" on mdadm-lab-server sudo mdadm /dev/md0 --add "$DISK1"
md_settle
assert_contains "the mirror is whole again" "$(on mdadm-lab-server cat /proc/mdstat || true)" "\[UU\]"
assert_contains "mdadm reports it clean" "$(on mdadm-lab-server sudo mdadm --detail /dev/md0 2>/dev/null || true)" "State : clean"

report_results "Exercise 3"
