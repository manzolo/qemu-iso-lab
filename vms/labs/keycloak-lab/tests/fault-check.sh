#!/usr/bin/env bash
# Opt-in destructive-to-this-lab checks. Not part of group test's test_NN_* glob.
# Run with the pristine lab running. Every mutation has an EXIT cleanup.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LOG_DIR="${KEYCLOAK_FAULT_LOG_DIR:-$(mktemp -d /tmp/keycloak-faults.XXXXXX)}"
mkdir -p "$LOG_DIR"
restore_command=''
restore() {
    if [[ -n "$restore_command" ]]; then
        on keycloak-lab-server "$restore_command"
        restore_command=''
    fi
}
trap restore EXIT
run_case() {
    local number="$1" expected="$2" status=0
    local scripts=("$HERE"/test_"$number"_*.sh)
    local log="$LOG_DIR/$number-$expected.log"
    bash "${scripts[0]}" >"$log" 2>&1 || status=$?
    if [[ "$expected" == fail ]]; then
        # A connection failure outside assert is not evidence that the test caught the fault.
        assert "exercise $number detects the introduced fault" test "$status" -gt 0
        assert "exercise $number reports a failed assertion" grep -q '\[FAIL\]' "$log"
    else
        assert "exercise $number passes after restoration" test "$status" -eq 0
    fi
    tail -n 3 "$log"
}
# Refuse to mutate an already broken lab.
for number in 01 02 03 04 05 06 07; do run_case "$number" pass; done
if [[ "$FAIL_COUNT" != 0 ]]; then report_results 'Fault-check baseline'; exit "$FAIL_COUNT"; fi

# 1: server process missing. Restore waits for the realm before the next case.
restore_command='sudo systemctl start keycloak; for i in $(seq 1 120); do curl -fsS http://127.0.0.1:8080/realms/lab/.well-known/openid-configuration >/dev/null 2>&1 && exit 0; sleep 2; done; exit 1'
on keycloak-lab-server sudo systemctl stop keycloak
run_case 01 fail
restore
run_case 01 pass

service_id=$(on keycloak-lab-server "kc-lab get clients -r lab -q clientId=lab-service --fields id | jq -r '.[0].id'")
web_id=$(on keycloak-lab-server "kc-lab get clients -r lab -q clientId=lab-web --fields id | jq -r '.[0].id'")
# 2: required service-account capability disabled.
restore_command="kc-lab update clients/$service_id -r lab -s serviceAccountsEnabled=true"
on keycloak-lab-server "kc-lab update clients/$service_id -r lab -s serviceAccountsEnabled=false"
run_case 02 fail
restore
run_case 02 pass

# 3, 6, 7: change the actual Keycloak secret, not an expected value inside a test.
restore_command="kc-lab update clients/$service_id -r lab -s secret=service-lab-secret"
on keycloak-lab-server "kc-lab update clients/$service_id -r lab -s secret=fault-injected-secret"
for number in 03 06 07; do run_case "$number" fail; done
restore
for number in 03 06 07; do run_case "$number" pass; done

# 4: the app can no longer exchange its authorization code.
restore_command="kc-lab update clients/$web_id -r lab -s secret=web-lab-secret"
on keycloak-lab-server "kc-lab update clients/$web_id -r lab -s secret=fault-injected-secret"
run_case 04 fail
restore
run_case 04 pass

# 5: authenticate Alice as usual, but remove her permission.
restore_command='kc-lab add-roles -r lab --uusername alice --rolename editor'
on keycloak-lab-server kc-lab remove-roles -r lab --uusername alice --rolename editor
run_case 05 fail
restore
run_case 05 pass
printf 'Evidence: %s\n' "$LOG_DIR"
report_results 'Deliberate faults and restoration'
