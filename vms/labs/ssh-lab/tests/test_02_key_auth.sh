#!/usr/bin/env bash
# Exercise 2 — an ed25519 and an RSA key pair on the client, read and removed. Leaves nothing behind.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 2 — key-based authentication${RESET}"; echo ""

cleanup() { on ssh-lab-client "rm -f ~/.ssh/lab_key ~/.ssh/lab_key.pub ~/.ssh/lab_rsa ~/.ssh/lab_rsa.pub" >/dev/null 2>&1 || true; }
cleanup; trap cleanup EXIT

assert "an ed25519 key pair is generated" on ssh-lab-client "ssh-keygen -t ed25519 -f ~/.ssh/lab_key -N '' -q"
assert "both key files exist" on ssh-lab-client "test -f ~/.ssh/lab_key && test -f ~/.ssh/lab_key.pub"
ed=$(on ssh-lab-client "ssh-keygen -l -f ~/.ssh/lab_key.pub" 2>/dev/null || true)
assert_contains "the key is ED25519" "$ed" "ED25519"
assert "a 4096-bit RSA key is generated" on ssh-lab-client "ssh-keygen -t rsa -b 4096 -f ~/.ssh/lab_rsa -N '' -q"
rsa=$(on ssh-lab-client "ssh-keygen -l -f ~/.ssh/lab_rsa.pub" 2>/dev/null || true)
assert_contains "the RSA key is 4096 bits" "$rsa" "^4096 "

report_results "Exercise 2"
