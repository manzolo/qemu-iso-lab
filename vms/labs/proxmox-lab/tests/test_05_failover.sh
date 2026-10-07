#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_pve.sh"
echo ""; echo "${BOLD}Exercise 5 — a node dies, the container moves${RESET}"; echo ""
pve_teardown; trap pve_teardown EXIT
replicate >/dev/null || true
pve "$N1" "ha-manager add ct:$CT --state started --max_relocate 1 --max_restart 1" >/dev/null
assert "HA manages CT 200 on node 1" wait_ha "$N1" started 120
"$VMCTL" stop "$N1" --force >/dev/null 2>&1 || true
assert "node 1 is gone" sh -c "! \"$VMCTL\" shell $N1 -- true"
# The dead node's lock expires after about two minutes, then HA fences it and starts the CT elsewhere.
assert "HA restarts CT 200 on node 2 or node 3" sh -c "for i in \$(seq 1 120); do \"$VMCTL\" shell $N2 -- \"ha-manager status | grep -Eq 'ct:$CT \\(proxmox-ve-node[23], started\\)'\" && exit 0; sleep 3; done; exit 1"
for _ in $(seq 1 20); do [ -n "$(it_tools)" ] && break; sleep 3; done
assert_contains "the client gets IT-Tools again, same address" "$(it_tools)" "IT Tools"
assert_contains "two nodes keep the quorum" "$(pve "$N2" pvecm status)" "^Quorate: +Yes$"
"$VMCTL" group up proxmox-lab >/dev/null 2>&1 || true
assert "node 1 comes back and rejoins" sh -c "for i in \$(seq 1 60); do \"$VMCTL\" shell $N2 -- \"pvecm status | grep -q '^Nodes: *3'\" && exit 0; sleep 5; done; exit 1"
report_results "Exercise 5"
