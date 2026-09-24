#!/bin/sh
# client-lan.sh MAC CIDR: static CIDR on the NIC whose address is MAC (the pve-lan segment).
# The segment NIC exists only in the runtime phase (vmctl start), so the NetworkManager profile
# is bound to the MAC, not to an interface name, and activates whenever that NIC appears.
set -eu
mac=$1
cidr=$2
nmcli connection delete pve-lan >/dev/null 2>&1 || true
nmcli connection add type ethernet con-name pve-lan ifname '*' ethernet.mac-address "$mac" \
    autoconnect yes ipv4.method manual ipv4.addresses "$cidr" ipv6.method disabled
if ip -o link show | grep -qi "$mac"; then
    nmcli connection up pve-lan
fi
nmcli -f connection.id,802-3-ethernet.mac-address,ipv4.addresses connection show pve-lan
