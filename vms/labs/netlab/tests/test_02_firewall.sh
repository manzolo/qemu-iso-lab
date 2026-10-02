#!/usr/bin/env bash
# Exercise 2 — Through the firewall (vms/labs/netlab/lab.json). Read-only.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 2 — Through the firewall${RESET}"; echo ""

route=$(on lubuntu-lab ip route 2>/dev/null || true)
assert_contains "the client's default route is the router" "$route" "^default via 192\.168\.0\.1 "
assert "HTTPS reaches the Internet through the pfSense NAT" on lubuntu-lab "curl -sI https://example.org | grep -q '^HTTP/'"
lan=$(on pfsense-lab ifconfig vtnet1 2>/dev/null || true)
assert_contains "pfSense holds the LAN address" "$lan" "inet 192\.168\.0\.1 "
assert "the forwards answer on the host" "$VMCTL" lab check

report_results "Exercise 2"
