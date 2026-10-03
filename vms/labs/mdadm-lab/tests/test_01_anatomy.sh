#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_mdadm.sh"
echo ""; echo "${BOLD}Exercise 1 — RAID anatomy${RESET}"; echo ""
md_teardown; trap md_teardown EXIT
assert_contains "mdadm is installed" "$(on mdadm-lab-server mdadm --version 2>&1 || true)" "mdadm"
assert_contains "four 2 GiB lab disks are attached" "$(echo "$LAB_DISKS" | wc -w)" "^4$"
assert_contains "/proc/mdstat lists the personalities" "$(on mdadm-lab-server cat /proc/mdstat || true)" "^Personalities"
assert_not_contains "no array exists yet" "$(on mdadm-lab-server cat /proc/mdstat || true)" "^md[0-9]"

report_results "Exercise 1"
