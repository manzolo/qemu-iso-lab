#!/usr/bin/env bash
# pxe-lab server (vms/labs/pxe-lab/): everything a diskless machine on the lab segment needs to
# install Debian over the network, the way qlab's pxe-lab builds it.
#
#   dnsmasq  DHCP + TFTP, on the lab segment (pxe-lan, 172.20.9.0/24) and nowhere else
#   iPXE     QEMU's NIC boot ROM is iPXE: it is told to fetch boot.ipxe over HTTP (option 175),
#            which chains pxelinux over TFTP so the menu and a real TFTP transfer happen
#   GRUB     a UEFI firmware (DHCP option 93, client-arch 7 or 9) gets Debian's shim + GRUB over
#            TFTP instead, and GRUB's own menu: pxelinux is a BIOS program
#   nginx    the Debian installer's kernel and initrd (40 MB: HTTP, not TFTP) and the answers
#            (preseed.cfg, late_command.sh) that vmctl writes for each install
#   NAT      the segment has no other way out: the installer reaches the Debian mirror through here
#
# Run as root. Re-running it is the reset: configuration and files are written again.
set -euo pipefail

LAN_IF=pxe-lan
LAN_IP=172.20.9.1
RANGE_START=172.20.9.100
RANGE_END=172.20.9.200
SUITE=trixie
TFTP=/srv/pxe/tftp
WWW=/srv/pxe/www
IMAGES="https://deb.debian.org/debian/dists/${SUITE}/main/installer-amd64/current/images"

[ "$(id -u)" = 0 ] || { echo "run as root: sudo bash $0" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "==> pxe-lab: directories"
install -d -m 755 "$TFTP/pxelinux.cfg" "$TFTP/debian-installer/amd64" "$WWW/debian" "$WWW/debian-efi"

# dnsmasq's configuration comes first: the package starts the service as it installs, and with
# its defaults it would bind port 53 everywhere, where systemd-resolved already listens.
echo "==> pxe-lab: dnsmasq configuration (DHCP + TFTP on $LAN_IF only)"
install -d -m 755 /etc/dnsmasq.d
cat > /etc/dnsmasq.d/pxe-lab.conf <<EOF
# pxe-lab: DHCP, DNS and TFTP for the lab segment, nothing else.
# bind-dynamic: listen on $LAN_IF's address only, and wait for the interface if it is not up yet.
interface=$LAN_IF
bind-dynamic
domain=pxe.lab
dhcp-authoritative
dhcp-range=$RANGE_START,$RANGE_END,255.255.255.0,12h
dhcp-option=option:router,$LAN_IP
dhcp-option=option:dns-server,$LAN_IP
log-dhcp

# Upstream DNS: systemd-resolved on the server. Ubuntu's dnsmasq service points it at a
# resolvconf file (-r /run/dnsmasq/resolv.conf) that does not exist without the resolvconf
# package, and with no upstream every lookup of the clients is REFUSED: the installer then
# stops on "Bad archive mirror" (verified live, 2026-10-16).
no-resolv
server=127.0.0.53

enable-tftp
tftp-root=$TFTP

# A plain BIOS PXE ROM gets pxelinux over TFTP. A UEFI firmware says so with option 93
# (client-arch 7 or 9, x86-64) and gets Debian's shim, which loads GRUB from the same directory.
# iPXE says who it is with option 175: it gets a script over HTTP instead, or dnsmasq would hand
# it the boot loader again, forever. The tags exclude each other: one boot file per client.
dhcp-match=set:ipxe,175
dhcp-match=set:efi64,option:client-arch,7
dhcp-match=set:efi64,option:client-arch,9
dhcp-boot=tag:!ipxe,tag:!efi64,lpxelinux.0
dhcp-boot=tag:!ipxe,tag:efi64,debian-installer/amd64/bootnetx64.efi
dhcp-boot=tag:ipxe,http://$LAN_IP/boot.ipxe
EOF

echo "==> pxe-lab: packages"
apt-get update -q
apt-get install -y -q dnsmasq nginx pxelinux syslinux-common iptables wget tcpdump

echo "==> pxe-lab: boot loader and menu (TFTP)"
cp -f /usr/lib/PXELINUX/lpxelinux.0 "$TFTP/"
for module in ldlinux.c32 menu.c32 libutil.c32 libcom32.c32; do
    cp -f "/usr/lib/syslinux/modules/bios/$module" "$TFTP/"
done
cat > "$TFTP/pxelinux.cfg/default" <<EOF
DEFAULT menu.c32
PROMPT 0
TIMEOUT 300
ONTIMEOUT install
MENU TITLE pxe-lab :: Debian 13 over the network

LABEL install
  MENU LABEL ^Install Debian (automated, preseed)
  MENU DEFAULT
  # Kernel and initrd over HTTP: lpxelinux.0 has an HTTP client, and the initrd is 40 MB.
  KERNEL http://$LAN_IP/debian/linux
  INITRD http://$LAN_IP/debian/initrd.gz
  # The network is configured before the preseed can be fetched, so the interface and the
  # language are answered here. BOOTIF (IPAPPEND 2) makes "auto" pick the NIC that netbooted,
  # not the client's other one. What follows --- goes to the installed system's kernel line.
  APPEND auto=true priority=critical url=http://$LAN_IP/debian/preseed.cfg netcfg/choose_interface=auto hostname=pxe-lab-client domain=pxe.lab locale=en_US.UTF-8 language=en country=US keymap=us DEBIAN_FRONTEND=text console=ttyS0,115200 --- console=ttyS0,115200
  IPAPPEND 2

LABEL local
  MENU LABEL Boot from the ^local disk
  LOCALBOOT 0
EOF

echo "==> pxe-lab: Debian installer ($SUITE) for the web root"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
wget -q -O "$work/SHA256SUMS" "$IMAGES/SHA256SUMS"
wget -q -O "$work/netboot.tar.gz" "$IMAGES/netboot/netboot.tar.gz"
expected=$(awk '$2 == "./netboot/netboot.tar.gz" {print $1}' "$work/SHA256SUMS")
actual=$(sha256sum "$work/netboot.tar.gz" | cut -d' ' -f1)
[ -n "$expected" ] && [ "$expected" = "$actual" ] || { echo "netboot.tar.gz: checksum mismatch" >&2; exit 1; }
tar -xzf "$work/netboot.tar.gz" -C "$work" ./debian-installer/amd64/linux ./debian-installer/amd64/initrd.gz \
    ./debian-installer/amd64/bootnetx64.efi ./debian-installer/amd64/grubx64.efi ./debian-installer/amd64/grub
install -m 644 "$work/debian-installer/amd64/linux" "$work/debian-installer/amd64/initrd.gz" "$WWW/debian/"

echo "==> pxe-lab: UEFI boot loader and menu (TFTP)"
# Debian's paths, kept under the TFTP root: the GRUB image looks for its modules and grub.cfg in
# debian-installer/amd64/grub on the server it came from.
rm -rf "$TFTP/debian-installer/amd64/grub"
cp -a "$work/debian-installer/amd64/bootnetx64.efi" "$work/debian-installer/amd64/grubx64.efi" \
    "$work/debian-installer/amd64/grub" "$TFTP/debian-installer/amd64/"
# The shim asks for grubx64.efi at the TFTP root when dnsmasq serves it (from a slirp bench it
# asked in its own directory): a copy there, and GRUB still finds its prefix (verified live).
cp -f "$work/debian-installer/amd64/grubx64.efi" "$TFTP/grubx64.efi"
cat > "$TFTP/debian-installer/amd64/grub/grub.cfg" <<'EOF'
# pxe-lab: the UEFI twin of pxelinux.cfg/default, for clients that netbooted with option 93.
serial --unit=0 --speed=115200
terminal_input console serial
terminal_output console serial
set default=0
set timeout=30
insmod http
insmod regexp

menuentry 'Install Debian (automated, preseed, UEFI)' {
    # BOOTIF in d-i's form (01-aa-bb-cc-dd-ee-ff) from the MAC GRUB netbooted with, so the
    # installer configures this NIC and not the client's other one.
    regexp --set=1:m1 --set=2:m2 --set=3:m3 --set=4:m4 --set=5:m5 --set=6:m6 '^(..):(..):(..):(..):(..):(..)$' "$net_default_mac"
    echo 'Loading the Debian installer over HTTP...'
    # Kernel and initrd over HTTP, like the BIOS menu. UEFI clients have their own answers
    # (debian-efi/): the BIOS client and this one can be installed one after the other.
    linux (http,LAN_IP)/debian/linux auto=true priority=critical url=http://LAN_IP/debian-efi/preseed.cfg netcfg/choose_interface=auto BOOTIF=01-$m1-$m2-$m3-$m4-$m5-$m6 hostname=pxe-lab-client-efi domain=pxe.lab locale=en_US.UTF-8 language=en country=US keymap=us DEBIAN_FRONTEND=text console=ttyS0,115200 --- console=ttyS0,115200
    initrd (http,LAN_IP)/debian/initrd.gz
}

menuentry 'Boot from the local disk' {
    exit
}
EOF
sed -i "s/LAN_IP/$LAN_IP/g" "$TFTP/debian-installer/amd64/grub/grub.cfg"

cat > "$WWW/boot.ipxe" <<EOF
#!ipxe
# QEMU's NIC boot ROM is iPXE, so the clients here land on this script. It hands over to
# pxelinux over TFTP, so the menu and a TFTP transfer really happen. Option 210 is pxelinux's
# path prefix: without it pxelinux looks for its modules under the HTTP URL it came from.
set 210:string tftp://$LAN_IP/
# pxelinux is a BIOS program: iPXE running on UEFI firmware takes GRUB, like the firmware's own PXE.
iseq \${platform} efi && goto efi || goto bios
:efi
chain tftp://$LAN_IP/debian-installer/amd64/bootnetx64.efi || goto direct
:bios
chain tftp://$LAN_IP/lpxelinux.0 || goto direct
:direct
# Neither boot loader started: boot the installer directly.
echo pxe-lab: pxelinux unavailable, booting the Debian installer directly
kernel http://$LAN_IP/debian/linux auto=true priority=critical url=http://$LAN_IP/debian/preseed.cfg netcfg/choose_interface=auto BOOTIF=01-\${netX/mac:hexhyp} hostname=pxe-lab-client domain=pxe.lab locale=en_US.UTF-8 language=en country=US keymap=us DEBIAN_FRONTEND=text console=ttyS0,115200 --- console=ttyS0,115200
initrd http://$LAN_IP/debian/initrd.gz
boot
EOF

echo "==> pxe-lab: nginx serves $WWW"
cat > /etc/nginx/sites-available/pxe-lab <<EOF
server {
    listen 80 default_server;
    root $WWW;
    autoindex on;
    access_log /var/log/nginx/pxe-lab.access.log;
}
EOF
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/pxe-lab /etc/nginx/sites-enabled/pxe-lab

echo "==> pxe-lab: NAT from the segment to the internet"
cat > /usr/local/sbin/pxe-lab-nat <<'EOF'
#!/bin/sh
# The segment's only way out: forward and masquerade through the interface of the default route.
set -eu
sysctl -qw net.ipv4.ip_forward=1
wan=$(ip -4 route show default | awk '{print $5; exit}')
[ -n "$wan" ] || { echo "pxe-lab-nat: no default route" >&2; exit 1; }
iptables -t nat -C POSTROUTING -o "$wan" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "$wan" -j MASQUERADE
EOF
chmod 755 /usr/local/sbin/pxe-lab-nat
cat > /etc/systemd/system/pxe-lab-nat.service <<'EOF'
[Unit]
Description=pxe-lab: NAT from the lab segment to the internet
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/pxe-lab-nat

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now pxe-lab-nat.service nginx
systemctl enable dnsmasq
systemctl restart nginx dnsmasq
echo "==> pxe-lab: server ready (dnsmasq waits for $LAN_IF, which comes up at the lab's runtime boot)"
