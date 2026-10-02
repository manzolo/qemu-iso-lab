#!/usr/bin/env bash
# Exercise 0 — the two machines, their tools and the segment between them. Read-only.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 1 — VPN anatomy${RESET}"; echo ""

for vm in vpn-lab-server vpn-lab-client; do
    assert "$vm has wg and wg-quick" on "$vm" "command -v wg wg-quick"
    assert "$vm has openvpn" on "$vm" command -v openvpn
done
assert "the server has tcpdump and iptables" on vpn-lab-server "command -v tcpdump iptables"
assert "the server holds 172.20.1.1 on the segment" on vpn-lab-server "ip -br addr | grep -q '172\.20\.1\.1/24'"
assert "the client holds 172.20.1.2 on the segment" on vpn-lab-client "ip -br addr | grep -q '172\.20\.1\.2/24'"
assert "server -> client over the segment" on vpn-lab-server ping -c1 -W3 172.20.1.2
assert "client -> server over the segment" on vpn-lab-client ping -c1 -W3 172.20.1.1

report_results "Exercise 1"
