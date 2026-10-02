#!/usr/bin/env bash
# Exercise 1 — what the server runs and what the client carries. Read-only.
# The client has no key on the server, so reachability is a TCP open to :22, not a login.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 1 — SSH anatomy${RESET}"; echo ""

assert "the SSH service is active on the server" on ssh-lab-server systemctl is-active ssh
cfg=$(on ssh-lab-server cat /etc/ssh/sshd_config)
assert_contains "sshd_config mentions PubkeyAuthentication" "$cfg" "PubkeyAuthentication"
assert "the client has ssh-keygen" on ssh-lab-client command -v ssh-keygen
assert "the client has sshpass, nmap and knock" on ssh-lab-client "command -v sshpass nmap knock"
assert_fail "sshd on the segment is gated closed until a knock (exercise 5)" on ssh-lab-client "timeout 3 bash -c 'echo > /dev/tcp/172.20.2.1/22'"

report_results "Exercise 1"
