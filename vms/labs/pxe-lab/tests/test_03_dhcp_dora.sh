#!/usr/bin/env bash
# Exercise 3 — a full DORA from the client's segment NIC, answered by dnsmasq (not by QEMU's NAT).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 3 — DORA, and who answered it${RESET}"; echo ""

nic=$(on pxe-lab-client "ip -o -4 addr | awk '/172\\.20\\.9\\./ {print \$2; exit}'")
assert_contains "the client has an address on the lab segment" "$nic" "^[a-z0-9]+$"
mac=$(on pxe-lab-client cat /sys/class/net/$nic/address)
log_info "segment NIC $nic, MAC $mac"
on pxe-lab-client "sudo ifdown $nic >/dev/null 2>&1; sudo ifup $nic >/dev/null 2>&1" || true
log=$(on pxe-lab-server sudo journalctl -u dnsmasq --since -2min --no-pager)
for message in DHCPDISCOVER DHCPOFFER DHCPREQUEST DHCPACK; do
    assert_contains "$message from or to $mac" "$log" "$message\\(pxe-lan\\).* $mac"
done
assert_contains "the router option points at the server" "$log" "option: *3 router *172\.20\.9\.1"
leases=$(on pxe-lab-server cat /var/lib/misc/dnsmasq.leases)
assert_contains "the lease is in dnsmasq's file" "$leases" "$mac 172\.20\.9\.(1[0-9][0-9]|200) "
addr=$(on pxe-lab-client ip -4 -o addr show $nic)
assert_contains "the client's address comes from the lab pool" "$addr" "172\.20\.9\.(1[0-9][0-9]|200)/24"

report_results "Exercise 3"
