#!/usr/bin/env bash
# Exercise 5 — a mounted ext4 volume grown online by 1 GiB, its data intact; then removed.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_lvm.sh"
echo ""; echo "${BOLD}Exercise 5 — grow a volume online${RESET}"; echo ""

lvm_teardown; trap lvm_teardown EXIT
lvm_vg
on lvm-lab-server "sudo lvcreate -qy -L 1G -n data labvg && sudo mkfs.ext4 -q /dev/labvg/data && sudo mkdir -p /mnt/lab-data && sudo mount /dev/labvg/data /mnt/lab-data && echo before-growth | sudo tee /mnt/lab-data/keep >/dev/null" >/dev/null
before=$(on lvm-lab-server df -BM --output=size /mnt/lab-data | tail -1 | tr -dc 0-9)
assert "lvextend -L +1G --resizefs while mounted" on lvm-lab-server sudo lvextend -q -L +1G --resizefs labvg/data
after=$(on lvm-lab-server df -BM --output=size /mnt/lab-data | tail -1 | tr -dc 0-9)
assert "the file system grew by about 1 GiB ($before -> $after MB)" test "$((after - before))" -gt 900
keep=$(on lvm-lab-server cat /mnt/lab-data/keep 2>/dev/null || true)
assert_contains "the data survived the growth" "$keep" "^before-growth$"
size=$(on lvm-lab-server sudo env LC_ALL=C lvs --noheadings --units g -o lv_size labvg/data 2>/dev/null || true)
assert_contains "lvs reports 2 GiB" "$size" "2\.00g"

report_results "Exercise 5"
