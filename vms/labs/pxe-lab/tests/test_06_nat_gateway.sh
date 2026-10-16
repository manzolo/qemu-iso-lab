#!/usr/bin/env bash
# Exercise 6 — the server is the segment's gateway: forwarding, masquerade, DNS; the client reaches the mirror through it.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 6 — the gateway${RESET}"; echo ""

assert_contains "the server forwards" "$(on pxe-lab-server sysctl -n net.ipv4.ip_forward)" "^1$"
nat=$(on pxe-lab-server sudo iptables -t nat -S POSTROUTING)
assert_contains "and masquerades through its NAT NIC" "$nat" "-o enp0s3 -j MASQUERADE"
nic=$(on pxe-lab-client "ip -o -4 addr | awk '/172\\.20\\.9\\./ {print \$2; exit}'")
code=$(on pxe-lab-client curl -s --max-time 20 --interface "$nic" -o /dev/null -w '%{http_code}' http://deb.debian.org/debian/dists/trixie/Release || true)
assert_contains "the client reaches the Debian mirror through the segment" "$code" "^200$"
answer=$(on pxe-lab-client getent ahostsv4 deb.debian.org || true)
assert_contains "names resolve" "$answer" "STREAM"

report_results "Exercise 6"
