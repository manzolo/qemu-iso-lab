#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_k8s.sh"
echo ""; echo "${BOLD}Exercise 4 — drain a node${RESET}"; echo ""
cleanup() { on "$M" "kubectl uncordon k8s-lab-node2; kubectl delete deployment web --ignore-not-found --wait=true" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
nodes_of_web() { k get pods -l app=web --field-selector=status.phase=Running -o 'jsonpath={range .items[*]}{.spec.nodeName}{"\n"}{end}'; }
k create deployment web --image=nginx:1.27-alpine --replicas=6 >/dev/null
assert "six replicas available" on "$M" kubectl rollout status deployment/web --timeout=300s
assert_contains "some replicas on k8s-lab-node2 before the drain" "$(nodes_of_web)" "^k8s-lab-node2$"
assert "the drain completes" on "$M" kubectl drain k8s-lab-node2 --ignore-daemonsets --delete-emptydir-data --timeout=180s
assert_contains "node2 is cordoned" "$(k get node k8s-lab-node2 -o 'jsonpath={.spec.unschedulable}')" "^true$"
assert "six replicas available again" on "$M" kubectl rollout status deployment/web --timeout=180s
assert_contains "no replica left on node2" "$(nodes_of_web | grep -c '^k8s-lab-node2$' || true)" "^0$"
assert_contains "calico-node stays on node2 (DaemonSet)" "$(k get pods -n kube-system -l k8s-app=calico-node --field-selector spec.nodeName=k8s-lab-node2 --no-headers)" " Running "
k uncordon k8s-lab-node2 >/dev/null
assert_contains "uncordon gives node2 back" "$(k get node k8s-lab-node2 -o 'jsonpath={.spec.unschedulable}')" "^$"
cleanup
report_results "Exercise 4"
