#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_pve.sh"
echo ""; echo "${BOLD}Exercise 4 — high availability, a planned move${RESET}"; echo ""
pve_teardown; trap pve_teardown EXIT
replicate >/dev/null || true
pve "$N1" "ha-manager add ct:$CT --state started --max_relocate 1 --max_restart 1" >/dev/null
assert "HA manages CT 200, started on node 1" wait_ha "$N1" started 120
assert_contains "the cluster has an HA master" "$(pve "$N1" "ha-manager status")" "^master proxmox-ve"
pve "$N1" "ha-manager migrate ct:$CT $N2" >/dev/null
assert "HA moves it to node 2" wait_ha "$N2" started 240
assert_contains "it runs on node 2 now" "$(pve "$N2" "pct status $CT")" "running"
assert_contains "same address: the client still gets IT-Tools" "$(it_tools)" "IT Tools"
pve "$N2" "ha-manager migrate ct:$CT $N1" >/dev/null
assert "and back to node 1" wait_ha "$N1" started 240
report_results "Exercise 4"
