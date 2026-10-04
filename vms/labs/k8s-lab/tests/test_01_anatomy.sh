#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_k8s.sh"
echo ""; echo "${BOLD}Exercise 1 — the cluster's anatomy${RESET}"; echo ""
nodes=$(k get nodes -o wide --no-headers)
for n in k8s-lab-main k8s-lab-node1 k8s-lab-node2; do
    assert_contains "$n is Ready" "$nodes" "^$n +Ready "
done
assert_contains "main reports its segment address" "$nodes" "^k8s-lab-main .* 172\.20\.6\.1 "
assert_contains "node1 reports its segment address" "$nodes" "^k8s-lab-node1 .* 172\.20\.6\.11 "
assert_contains "node2 reports its segment address" "$nodes" "^k8s-lab-node2 .* 172\.20\.6\.12 "
assert_contains "no node on the NAT address" "$(printf '%s\n' "$nodes" | grep -c '10\.0\.2\.15' || true)" "^0$"
calico=$(k get pods -n kube-system -l k8s-app=calico-node --no-headers)
assert_contains "calico-node runs on three nodes" "$(printf '%s\n' "$calico" | grep -c ' Running ' || true)" "^3$"
assert_contains "CoreDNS runs" "$(k get pods -n kube-system -l k8s-app=kube-dns --no-headers)" " Running "
assert_contains "node1 is a worker" "$(on k8s-lab-node1 microk8s status 2>&1 || true)" "acting as a node"
report_results "Exercise 1"
