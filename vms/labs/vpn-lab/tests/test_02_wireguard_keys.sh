#!/usr/bin/env bash
# Exercise 1, first half — WireGuard key pairs on both ends. Leaves nothing behind.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 2 — WireGuard keys${RESET}"; echo ""

for vm in vpn-lab-server vpn-lab-client; do
    priv=$(on "$vm" wg genkey)
    pub=$(printf '%s\n' "$priv" | on "$vm" wg pubkey)
    assert_contains "$vm: private key is base64" "$priv" "^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=$"
    assert_contains "$vm: public key is base64" "$pub" "^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw480]=$"
    assert_not_contains "$vm: the public key differs from the private one" "$pub" "^${priv//+/\\+}$"
done

report_results "Exercise 2"
