#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_pve.sh"
echo ""; echo "${BOLD}Exercise 2 — the containers${RESET}"; echo ""
pve_teardown
cts=$(pve "$N1" pct list)
assert_contains "IT-Tools (CT 200) runs on node 1" "$cts" "^200 +running +it-tools"
assert_contains "Glance (CT 201) runs on node 1" "$cts" "^201 +running +glance"
assert_contains "CT 200's disk is a ZFS dataset" "$(pve "$N1" "pct config 200 | grep ^rootfs")" "local-zfs:subvol-200-disk-[0-9]+"
assert_contains "CT 200 has its lab address" "$(pve "$N1" "pct exec 200 -- ip -4 -o addr show eth1")" "10\.10\.10\.20/24"
assert_contains "the client gets IT-Tools" "$(it_tools)" "IT Tools"
assert_contains "the client gets Glance" "$(pve "$C" "curl -s -m 5 -o /dev/null -w '%{http_code}' http://10.10.10.21:8080/")" "^200$"
assert_contains "node 2 holds no copy of CT 200 yet" "$(pve "$N2" "zfs list -H -o name | grep -c subvol-200 || true")" "^0$"
report_results "Exercise 2"
