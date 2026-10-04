#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_k8s.sh"
echo ""; echo "${BOLD}Exercise 2 — a Deployment and a NodePort Service${RESET}"; echo ""
cleanup() { on "$M" "kubectl delete service web --ignore-not-found; kubectl delete deployment web --ignore-not-found --wait=true" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
k create deployment web --image=nginx:1.27-alpine --replicas=3 >/dev/null
assert "the three replicas become available" on "$M" kubectl rollout status deployment/web --timeout=300s
k create service nodeport web --tcp=80:80 --node-port=30080 >/dev/null
spread=$(k get pods -l app=web -o 'jsonpath={range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort -u | grep -c . || true)
assert_contains "the replicas run on more than one node" "$spread" "^[23]$"
codes=$(on "$M" "for ip in \$(kubectl get pods -l app=web -o jsonpath='{.items[*].status.podIP}'); do curl -s -o /dev/null -w '%{http_code} ' --max-time 3 http://\$ip; done" 2>&1 || true)
assert_contains "every pod answers from main, whatever its node" "$codes" "^200 200 200 $"
for ip in 172.20.6.1 172.20.6.11 172.20.6.12; do
    page=""
    for _ in $(seq 1 15); do
        page=$(on "$M" "curl -s --max-time 3 http://$ip:30080" 2>/dev/null || true)
        [[ "$page" == *"Welcome to nginx"* ]] && break
        sleep 2
    done
    assert_contains "NodePort 30080 answers on $ip" "$page" "Welcome to nginx"
done
assert_contains "the host reaches it on 127.0.0.1:8089" "$(curl -s --max-time 5 http://127.0.0.1:8089 || true)" "Welcome to nginx"
first=$(k get pods -l app=web -o name | head -n1)
k delete "$first" --wait=true >/dev/null
assert "a deleted pod is replaced" on "$M" kubectl rollout status deployment/web --timeout=120s
assert_contains "three replicas again" "$(k get deployment web -o 'jsonpath={.status.readyReplicas}')" "^3$"
cleanup
assert_contains "the default namespace is empty again" "$(k get deployment,service -l app=web --no-headers)" "No resources found"
report_results "Exercise 2"
