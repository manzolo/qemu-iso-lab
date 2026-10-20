#!/usr/bin/env bash
# Preserve probe diagnostics on failure; assert itself intentionally suppresses stdout.
PROBE="$(dirname "${BASH_SOURCE[0]}")/_probe.py"
probe() {
    local output status=0
    output=$(on keycloak-lab-client python3 - "$1" < "$PROBE" 2>&1) || status=$?
    printf '%s\n' "$output"
    assert "$1: real HTTP flow matches the fixture" test "$status" -eq 0
}
