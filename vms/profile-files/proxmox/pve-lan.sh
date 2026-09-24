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
if grep -q '^auto vmbr1$' /etc/network/interfaces && ! grep -q "learning off" /etc/network/interfaces; then
    sed -i "/^#vmctl lab segment pve-lan/i\\	post-up bridge link set dev $name learning off" /etc/network/interfaces
fi
if ! grep -q '^auto vmbr1$' /etc/network/interfaces; then
    cat >> /etc/network/interfaces <<STANZA

iface $name inet manual

auto vmbr1
iface vmbr1 inet static
	address $cidr
	bridge-ports $name
	bridge-stp off
	bridge-fd 0
	# The segment is a QEMU multicast socket that echoes every frame back to its sender, so
	# the bridge would learn the containers' MACs on this port and send their traffic back
	# into the segment (TCP to 10.10.10.20 answered nothing, verified live): no learning here.
	post-up bridge link set dev $name learning off
#vmctl lab segment pve-lan
STANZA
fi
# The initramfs udev names the NICs first: it must carry the .link file too.
update-initramfs -u -k all
# Bring vmbr1 up now, without its port: the segment NIC exists only in the runtime phase, but the
# containers' second NIC needs the bridge to exist (pct refuses a missing one, even offline).
# ifreload reports the missing port and exits 1; the bridge and its address are there anyway.
ifreload -a || true
ip link show vmbr1 >/dev/null
grep -A5 '^auto vmbr1$' /etc/network/interfaces
