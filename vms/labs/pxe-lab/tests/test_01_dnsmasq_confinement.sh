#!/usr/bin/env bash
# Exercise 1 — dnsmasq answers DHCP, DNS and TFTP on the lab segment and nowhere else.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 1 — dnsmasq, confined to pxe-lan${RESET}"; echo ""

assert "dnsmasq is active" on pxe-lab-server systemctl is-active --quiet dnsmasq
addrs=$(on pxe-lab-server ip -4 -o addr show pxe-lan)
assert_contains "pxe-lan holds the server address 172.20.9.1" "$addrs" "172\.20\.9\.1/24"
conf=$(on pxe-lab-server cat /etc/dnsmasq.d/pxe-lab.conf)
assert_contains "it listens on the lab segment" "$conf" "^interface=pxe-lan$"
assert_contains "and binds only that interface's address" "$conf" "^bind-dynamic$"
assert_contains "the pool is 172.20.9.100-200" "$conf" "^dhcp-range=172\.20\.9\.100,172\.20\.9\.200,"
assert_contains "TFTP is on" "$conf" "^enable-tftp$"
assert_contains "iPXE is recognised by option 175" "$conf" "^dhcp-match=set:ipxe,175$"
assert_contains "iPXE is sent to HTTP" "$conf" "^dhcp-boot=tag:ipxe,http://172\.20\.9\.1/boot\.ipxe$"
assert_contains "every DHCP option is logged" "$conf" "^log-dhcp$"
assert_contains "upstream DNS is the server's resolver, not a missing resolvconf file" "$conf" "^server=127\.0\.0\.53$"
sockets=$(on pxe-lab-server sudo ss -ulpnH)
assert_contains "DHCP (67) is bound to pxe-lan" "$sockets" "%pxe-lan:67 .*dnsmasq"
assert_contains "TFTP (69) listens on 172.20.9.1" "$sockets" "172\.20\.9\.1:69 .*dnsmasq"
assert_not_contains "nothing of dnsmasq on the NAT address" "$sockets" "10\.0\.2\.15:(53|67|69) .*dnsmasq"
assert_not_contains "no wildcard DHCP socket" "$sockets" "0\.0\.0\.0:67 .*dnsmasq"

report_results "Exercise 1"
