#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_keycloak.sh"   # waits for Keycloak and the app after a cold boot
# Work in a uniquely named practice realm. Never reset the lab fixture before checking it.
R="test-practice-$$"
cleanup() { on keycloak-lab-server kc-lab delete "realms/$R" >/dev/null 2>&1 || true; }
trap cleanup EXIT
assert 'fixture contains Alice, Bob, editor and both configured clients' on keycloak-lab-server "
    kc-lab get users -r lab | jq -e 'map(.username) | sort == [\"alice\",\"bob\"]' &&
    kc-lab get roles/editor -r lab | jq -e '.name == \"editor\"' &&
    kc-lab get clients -r lab -q clientId=lab-web | jq -e '.[0].attributes[\"pkce.code.challenge.method\"] == \"S256\"' &&
    kc-lab get clients -r lab -q clientId=lab-service | jq -e '.[0].serviceAccountsEnabled == true'
"
assert 'kcadm creates realm, client, user and role, assigns and revokes it' on keycloak-lab-server "bash -se" <<SCRIPT
kc-lab create realms -s realm=$R -s enabled=true
kc-lab create clients -r $R -s clientId=practice-web -s publicClient=true -s standardFlowEnabled=true
kc-lab create roles -r $R -s name=reader
kc-lab create users -r $R -s username=learner -s enabled=true
kc-lab set-password -r $R --username learner --new-password learner-lab-pass
kc-lab add-roles -r $R --uusername learner --rolename reader
kc-lab get-roles -r $R --uusername learner | jq -e 'any(.[]; .name == "reader")'
kc-lab remove-roles -r $R --uusername learner --rolename reader
kc-lab get-roles -r $R --uusername learner | jq -e 'all(.[]; .name != "reader")'
SCRIPT
report_results 'Exercise 2 — realm, client, users, roles'
