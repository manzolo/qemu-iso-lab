#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_k8s.sh"
echo ""; echo "${BOLD}Exercise 3 — scale, rolling update, rollback${RESET}"; echo ""
cleanup() { on "$M" "kubectl delete deployment web --ignore-not-found --wait=true" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
image() { k get deployment web -o 'jsonpath={.spec.template.spec.containers[0].image}'; }
k create deployment web --image=nginx:1.27-alpine --replicas=3 >/dev/null
assert "three replicas available" on "$M" kubectl rollout status deployment/web --timeout=300s
k scale deployment web --replicas=5 >/dev/null
assert "scaled to five" on "$M" kubectl rollout status deployment/web --timeout=180s
assert_contains "five ready replicas" "$(k get deployment web -o 'jsonpath={.status.readyReplicas}')" "^5$"
k set image deployment/web nginx=nginx:1.28-alpine >/dev/null
assert "the rolling update completes" on "$M" kubectl rollout status deployment/web --timeout=300s
assert_contains "the template runs nginx:1.28-alpine" "$(image)" "^nginx:1\.28-alpine$"
assert_contains "every pod runs the new image" "$(k get pods -l app=web -o 'jsonpath={range .items[*]}{.spec.containers[0].image}{"\n"}{end}' | sort -u)" "^nginx:1\.28-alpine$"
assert_contains "two revisions in the history" "$(k rollout history deployment/web)" "^2 "
k rollout undo deployment/web >/dev/null
assert "the rollback completes" on "$M" kubectl rollout status deployment/web --timeout=300s
assert_contains "back on nginx:1.27-alpine" "$(image)" "^nginx:1\.27-alpine$"
cleanup
assert_contains "the Deployment is gone" "$(k get deployment web 2>&1)" "NotFound"
report_results "Exercise 3"
