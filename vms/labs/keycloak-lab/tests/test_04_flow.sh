#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/_keycloak.sh"
probe flow
status=0
on keycloak-lab-client python3 - /opt/keycloak-lab/app.py < "$LAB_COMMON_DIR/../../tests/test_keycloak_app.py" || status=$?
assert 'app rejects wrong issuer/audience/nonce/state and forged logout' test "$status" -eq 0
report_results 'Exercise 4 — flow'
