#!/bin/sh
# pve-lan.sh MAC CIDR: bridge vmbr1 with CIDR on the NIC whose address is MAC (the lab segment).
# The Proxmox installer configures one NIC (vmbr0, the NAT one). The segment NIC exists only in
# the runtime phase (vmctl start), not during the post-install boot, so it is named by a systemd
# .link file matching its MAC and the bridge refers to that fixed name: both apply at next boot.
set -eu
mac=$1
cidr=$2
name=pvelan0
cat > /etc/systemd/network/10-vmctl-pvelan.link <<LINK
[Match]
MACAddress=$mac

[Link]
Name=$name
LINK
if ! grep -q '^auto vmbr1$' /etc/network/interfaces; then
    cat >> /etc/network/interfaces <<STANZA

iface $name inet manual

auto vmbr1
iface vmbr1 inet static
	address $cidr
	bridge-ports $name
	bridge-stp off
	bridge-fd 0
#vmctl lab segment pve-lan
STANZA
fi
# The initramfs udev names the NICs first: it must carry the .link file too.
update-initramfs -u -k all
grep -A5 '^auto vmbr1$' /etc/network/interfaces
