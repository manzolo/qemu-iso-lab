#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_k8s.sh"
echo ""; echo "${BOLD}Exercise 5 — ConfigMap and Secret${RESET}"; echo ""
cleanup() { on "$M" "kubectl delete pod env-demo --ignore-not-found --grace-period=1 --wait=true; kubectl delete configmap web-config --ignore-not-found; kubectl delete secret web-secret --ignore-not-found" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
k create configmap web-config --from-literal=GREETING=hello --from-literal=COLOR=blue >/dev/null
k create secret generic web-secret --from-literal=PASSWORD=labsecret >/dev/null
# The pod's manifest on stdin, like the exercise's heredoc.
on "$M" kubectl apply -f - >/dev/null <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: env-demo
spec:
  containers:
  - name: show
    image: busybox:1.36
    command: ["sh", "-c", "echo GREETING=$GREETING COLOR=$COLOR PASSWORD=$PASSWORD; sleep 3600"]
    envFrom:
    - configMapRef: {name: web-config}
    - secretRef: {name: web-secret}
    volumeMounts:
    - {name: config, mountPath: /config}
  volumes:
  - name: config
    configMap: {name: web-config}
YAML
assert "the pod becomes Ready" on "$M" kubectl wait --for=condition=Ready pod/env-demo --timeout=180s
assert_contains "ConfigMap and Secret arrive as environment" "$(k logs env-demo)" "^GREETING=hello COLOR=blue PASSWORD=labsecret$"
assert_contains "one file per key in the volume" "$(k exec env-demo -- ls /config | tr '\n' ' ')" "^COLOR GREETING $"
assert_contains "the file holds the value" "$(k exec env-demo -- cat /config/COLOR)" "^blue$"
assert_contains "a Secret is only base64" "$(on "$M" "kubectl get secret web-secret -o jsonpath='{.data.PASSWORD}' | base64 -d")" "^labsecret$"
cleanup
assert_contains "pod, ConfigMap and Secret gone" "$(k get pod/env-demo configmap/web-config secret/web-secret 2>&1 | grep -c NotFound || true)" "^3$"
report_results "Exercise 5"
