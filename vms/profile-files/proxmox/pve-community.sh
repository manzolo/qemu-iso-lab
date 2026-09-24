#!/bin/sh
# pve-community.sh CTID APP HOSTNAME PORT NAT_CIDR LAN_CIDR: one LXC from the Proxmox VE
# Helper-Scripts (community-scripts.org, ct/APP.sh from the main branch, not pinned) created without
# prompts at NAT_CIDR on vmbr0, checked on its web PORT, then given a second NIC on the lab segment
# (vmbr1) at LAN_CIDR.
#
# Unattended: mode=default skips the whiptail menu (without it a missing TTY makes the script
# print "User exited script" and exit 0), TERM=xterm keeps clear/whiptail from failing, and the
# diagnostics question is pre-answered in /usr/local/community-scripts/diagnostics. The container
# goes on vmbr0, the NAT NIC the template and package downloads need, with a STATIC address: the
# Proxmox installer turned its own DHCP lease into a static 10.0.2.15, so slirp's DHCP server
# believes .15 is free and hands it to the first container, and the duplicate address cut the
# host off (verified live). Slirp routes any address of 10.0.2.0/24; its DHCP pool is .15-.30, so the
# lab uses .100 and up; 10.0.2.2 is its gateway and 10.0.2.3 its DNS.
set -eu
ctid=$1
app=$2
host=$3
port=$4
nat=$5
lan=$6
net0="name=eth0,bridge=vmbr0,ip=$nat,gw=10.0.2.2"
log="/root/community-$app.log"
mkdir -p /usr/local/community-scripts
[ -f /usr/local/community-scripts/diagnostics ] || echo DIAGNOSTICS=no > /usr/local/community-scripts/diagnostics
if pct status "$ctid" >/dev/null 2>&1; then
    echo "pve-community: CT $ctid already exists, keeping it"
    pct config "$ctid" | grep -q "^net0:.*ip=$nat" || { pct shutdown "$ctid" --timeout 60 2>/dev/null || true; pct set "$ctid" -net0 "$net0" -nameserver 10.0.2.3; }
else
    script=$(curl -fsSL "https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/$app.sh")
    if ! env TERM=xterm mode=default var_ctid="$ctid" var_hostname="$host" var_brg=vmbr0 var_net="$nat" var_gateway=10.0.2.2 var_ns=10.0.2.3 \
            var_container_storage=local-zfs var_template_storage=local \
            bash -c "$script" < /dev/null > "$log" 2>&1; then
        tail -n 40 "$log"
        echo "pve-community: ct/$app.sh failed, full log in $log" >&2
        exit 1
    fi
    if grep -q "User exited script" "$log" || ! pct status "$ctid" >/dev/null 2>&1; then
        tail -n 40 "$log"
        echo "pve-community: ct/$app.sh did not create CT $ctid (log: $log)" >&2
        exit 1
    fi
fi
pct start "$ctid" >/dev/null 2>&1 || true
ip=${nat%/*}
i=0
until curl -fsS -o /dev/null --max-time 5 "http://$ip:$port/"; do
    i=$((i + 1))
    [ $i -lt 40 ] || { echo "pve-community: $app does not answer on http://$ip:$port/" >&2; exit 1; }
    sleep 3
done
echo "pve-community: $app answers on http://$ip:$port/ (CT $ctid, NAT)"
pct set "$ctid" -onboot 1
# vmbr1 exists from pve-lan.sh on, also in the post-install boot where its segment port is missing.
if ! pct config "$ctid" | grep -q '^net1:'; then
    pct set "$ctid" -net1 "name=eth1,bridge=vmbr1,ip=$lan"
fi
pct config "$ctid" | grep -E '^(hostname|net[01]|onboot):'
