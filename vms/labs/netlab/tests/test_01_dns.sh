#!/usr/bin/env bash
# Exercise 1 — DNS and DHCP from Pi-hole (vms/labs/netlab/lab.json). Read-only.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 1 — DNS and DHCP from Pi-hole${RESET}"; echo ""

assert "pihole-FTL is active" on pihole-lab systemctl is-active pihole-FTL
status=$(on pihole-lab pihole status 2>/dev/null || true)
assert_contains "blocking is enabled" "$status" "nabled"
assert "Pi-hole is the LAN's DHCP server" on pihole-lab "sudo pihole-FTL --config dhcp.active | grep -q true"

answer=$(on lubuntu-lab resolvectl query pfsense.qlan 2>/dev/null || true)
assert_contains "the client resolves pfsense.qlan to the router" "$answer" "192\.168\.0\.1"
assert "the client resolves a public name" on lubuntu-lab "resolvectl query example.org >/dev/null"

report_results "Exercise 1"
