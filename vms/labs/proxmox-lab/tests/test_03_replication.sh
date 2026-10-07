#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_pve.sh"
echo ""; echo "${BOLD}Exercise 3 — ZFS replication${RESET}"; echo ""
pve_teardown; trap pve_teardown EXIT
assert "two replication jobs run and end OK" replicate
assert_contains "the jobs target node 2 and node 3" "$(pve "$N1" pvesr list)" "200-1 +local/$N3"
# The volume's name comes from the configuration: after a failover it can be disk-1, disk-2...
vol=$(pve "$N1" "pct config $CT | sed -n 's/^rootfs: local-zfs:\\([^,]*\\).*/\\1/p'")
for n in "$N2" "$N3"; do
    assert_contains "$n holds a copy of CT 200's disk ($vol)" "$(pve "$n" "zfs list -H -o name")" "^rpool/data/$vol\$"
    assert_contains "$n holds the replication snapshot" "$(pve "$n" "zfs list -H -t snapshot -o name")" "^rpool/data/$vol@__replicate_200-"
done
assert_contains "CT 200 still runs on node 1 only" "$(pve "$N2" "pct list | grep -c '^200 ' || true")" "^0$"
report_results "Exercise 3"
