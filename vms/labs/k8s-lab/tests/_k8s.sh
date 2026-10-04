#!/usr/bin/env bash
# Shared by the k8s-lab tests. The workers join at their first boot on the segment, which
# `vmctl group test` may have just started: every test waits for three Ready nodes, up to 10 min.
M=k8s-lab-main
k() { on "$M" kubectl "$@" 2>&1 || true; }
# First the boot-time unit of every node (k8s-lab-node.service, oneshot: active once it has finished).
for vm in k8s-lab-main k8s-lab-node1 k8s-lab-node2; do
    for _ in $(seq 1 120); do
        [ "$(on "$vm" systemctl is-active k8s-lab-node.service 2>/dev/null || true)" = "active" ] && break
        sleep 5
    done
done
for _ in $(seq 1 120); do
    [ "$(on "$M" "kubectl get nodes --no-headers 2>/dev/null | grep -c ' Ready '" 2>/dev/null || true)" = "3" ] && break
    sleep 5
done
# Ready comes before the pod network: a joined worker starts its calico-node afterwards, and the
# control plane's interface setting rolls the daemonset (test_01 met 2 of 3 Running, 2026-10-04).
on "$M" kubectl -n kube-system rollout status daemonset/calico-node --timeout=300s >/dev/null 2>&1 || true
