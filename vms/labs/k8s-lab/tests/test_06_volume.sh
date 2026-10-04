#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_k8s.sh"
echo ""; echo "${BOLD}Exercise 6 — a persistent volume: MariaDB that survives its pod${RESET}"; echo ""
cleanup() { on "$M" "kubectl delete deployment mariadb --ignore-not-found --wait=true; kubectl delete pvc mariadb-data --ignore-not-found --wait=true; kubectl delete secret mariadb-root --ignore-not-found" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
# hostpath-storage is the exercise's first step and stays enabled (it is idempotent).
on "$M" microk8s enable hostpath-storage >/dev/null 2>&1 || true
assert "a default StorageClass exists" on "$M" "kubectl get storageclass | grep -q '(default)'"
k create secret generic mariadb-root --from-literal=password=labroot >/dev/null
# The manifest the exercise applies, copied by the install into the guest's ~/k8s/.
k apply -f k8s/mariadb.yaml >/dev/null
assert "MariaDB comes up" on "$M" kubectl rollout status deployment/mariadb --timeout=300s
assert_contains "the claim is Bound" "$(k get pvc mariadb-data -o 'jsonpath={.status.phase}')" "^Bound$"
# mysqld answers a little after the container starts: retry the first statement.
db() { on "$M" "kubectl exec deploy/mariadb -- mariadb -uroot -plabroot -N -e \"$1\"" 2>&1 || true; }
for _ in $(seq 1 30); do db "SELECT 1" | grep -qx 1 && break; sleep 3; done
db "CREATE DATABASE shop; CREATE TABLE shop.items (name VARCHAR(20)); INSERT INTO shop.items VALUES ('kept');" >/dev/null
assert_contains "the row is written" "$(db 'SELECT name FROM shop.items')" "^kept$"
first=$(k get pods -l app=mariadb -o 'jsonpath={.items[0].metadata.name}')
k delete pod -l app=mariadb --wait=true >/dev/null
assert "a new pod comes up" on "$M" kubectl rollout status deployment/mariadb --timeout=300s
second=$(k get pods -l app=mariadb -o 'jsonpath={.items[0].metadata.name}')
assert "it is another pod ($first -> $second)" test "$second" != "$first"
for _ in $(seq 1 30); do db "SELECT 1" | grep -qx 1 && break; sleep 3; done
assert_contains "the row survived the pod" "$(db 'SELECT name FROM shop.items')" "^kept$"
cleanup
# The provisioner deletes the released volume a few seconds after the claim.
for _ in $(seq 1 20); do
    [ "$(k get pv --no-headers 2>&1 | grep -c mariadb-data || true)" = "0" ] && break
    sleep 3
done
assert_contains "the volume went with the claim" "$(k get pv --no-headers 2>&1 | grep -c mariadb-data || true)" "^0$"
report_results "Exercise 6"
