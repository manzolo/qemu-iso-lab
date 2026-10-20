#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_keycloak.sh"   # waits for Keycloak and the app after a cold boot
assert 'Keycloak and PostgreSQL services are active' on keycloak-lab-server 'systemctl is-active --quiet keycloak postgresql'
assert 'official Keycloak version is 26.4.7' on keycloak-lab-server '/opt/keycloak/bin/kc.sh --version | grep -F "26.4.7"'
assert 'Keycloak uses PostgreSQL, not the development H2 store' on keycloak-lab-server "sudo -u postgres psql -d keycloak -tAc \"SELECT COUNT(*) FROM realm WHERE name='lab'\" | grep -qx 1"
assert 'Keycloak listens on port 8080' on keycloak-lab-server "ss -ltn | grep -E ':8080 '"
assert 'admin console responds through the host forward' curl -fsS http://127.0.0.1:18080/admin/master/console/
assert 'kcadm authenticates and reads the lab realm' on keycloak-lab-server "kc-lab get realms/lab --fields realm | jq -e '.realm == \"lab\"'"
assert 'client app is active' on keycloak-lab-client systemctl is-active --quiet oidc-app
report_results 'Exercise 1 — anatomy'
