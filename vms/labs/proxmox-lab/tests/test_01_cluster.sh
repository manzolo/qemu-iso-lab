#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_pve.sh"
echo ""; echo "${BOLD}Exercise 1 — the cluster${RESET}"; echo ""
status=$(pve "$N1" pvecm status)
assert_contains "the cluster is pve-lab" "$status" "^Name: +pve-lab$"
assert_contains "three nodes" "$status" "^Nodes: +3$"
assert_contains "quorate" "$status" "^Quorate: +Yes$"
links=$(pve "$N1" "grep ring0_addr /etc/pve/corosync.conf")
assert_contains "corosync runs on the lab segment" "$(printf '%s\n' "$links" | grep -c '10\.10\.10\.[234]$' || true)" "^3$"
# After a join a node's authorized_keys is the cluster's file: vmctl's own key must be in it too.
for n in "$N1" "$N2" "$N3"; do
    assert_contains "vmctl reaches $n" "$(pve "$n" hostname)" "^$n$"
done
assert_contains "the client reaches node 3 on the segment" "$(pve "$C" "ping -c1 -W2 10.10.10.4 >/dev/null && echo ok")" "^ok$"
report_results "Exercise 1"
