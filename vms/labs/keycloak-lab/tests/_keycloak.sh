#!/usr/bin/env bash
# Preserve probe diagnostics on failure; assert itself intentionally suppresses stdout.
PROBE="$(dirname "${BASH_SOURCE[0]}")/_probe.py"
# The runner waits for SSH; Keycloak (Java) listens 12-24 s after that, longer under nested KVM (the recording
# studio, 2026-10-20: tests run right after `group install` found no realm and failed 8 checks). Wait, with a
# ceiling, for the realm's discovery on the server and for the app on the client; a stopped service still fails.
wait_for_stack() {
    local i
    for i in $(seq 1 90); do
        on keycloak-lab-server curl -fsS http://127.0.0.1:8080/realms/lab/.well-known/openid-configuration >/dev/null 2>&1 \
            && on keycloak-lab-client curl -fsS -o /dev/null http://127.0.0.1:8000/ 2>/dev/null && return 0
        sleep 2
    done
    echo "  [warn] keycloak-lab: the stack did not answer within 180 s; the checks run anyway"
    return 0
}
wait_for_stack
probe() {
    local output status=0
    output=$(on keycloak-lab-client python3 - "$1" < "$PROBE" 2>&1) || status=$?
    printf '%s\n' "$output"
    assert "$1: real HTTP flow matches the fixture" test "$status" -eq 0
}
