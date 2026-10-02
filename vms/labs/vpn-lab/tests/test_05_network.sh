#!/usr/bin/env bash
# Exercise 4 — the server fenced by iptables: only established, SSH and the VPN ports get in; flushed at the end.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 5 — firewall around the VPN${RESET}"; echo ""

cleanup() { on vpn-lab-server "sudo iptables -F INPUT" >/dev/null 2>&1 || true; }
cleanup
trap cleanup EXIT

assert "the client reaches the server before any rule" on vpn-lab-client ping -c1 -W2 172.20.1.1
on vpn-lab-server "sudo iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT && sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT && sudo iptables -A INPUT -p udp --dport 51820 -j ACCEPT && sudo iptables -A INPUT -p udp --dport 1194 -j ACCEPT && sudo iptables -A INPUT -j DROP"
rules=$(on vpn-lab-server sudo iptables -S INPUT)
assert_contains "the rules are in place" "$rules" "^-A INPUT -j DROP$"
assert_contains "SSH stays allowed" "$rules" "dport 22 -j ACCEPT"
assert "SSH to the server still works with the rules in" on vpn-lab-server true
assert_fail "ping from the client is dropped" on vpn-lab-client ping -c1 -W2 172.20.1.1
cleanup
assert "flushing the rules opens the server again" on vpn-lab-client ping -c1 -W2 172.20.1.1

report_results "Exercise 5"
