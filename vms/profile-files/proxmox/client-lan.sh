#!/bin/sh
# client-lan.sh MAC CIDR URL: static CIDR on the NIC whose address is MAC (the pve-lan segment),
# and Firefox opening URL (the Proxmox web GUI) at every desktop login.
# The segment NIC exists only in the runtime phase (vmctl start), so the NetworkManager profile
# is bound to the MAC, not to an interface name, and activates whenever that NIC appears.
set -eu
mac=$1
cidr=$2
url=$3
nmcli connection delete pve-lan >/dev/null 2>&1 || true
nmcli connection add type ethernet con-name pve-lan ifname '*' ethernet.mac-address "$mac" \
    autoconnect yes ipv4.method manual ipv4.addresses "$cidr" ipv6.method disabled
if ip -o link show | grep -qi "$mac"; then
    nmcli connection up pve-lan
fi
nmcli -f connection.id,802-3-ethernet.mac-address,ipv4.addresses connection show pve-lan

# The lab starts both VMs together and Proxmox takes longer to answer than this desktop to
# log in, so the launcher waits (up to 10 minutes) before opening the browser on the GUI.
cat > /usr/local/bin/vmctl-open-proxmox <<LAUNCHER
#!/bin/sh
i=0
while [ \$i -lt 200 ] && ! curl -ksf -o /dev/null --max-time 3 "$url"; do
    i=\$((i + 1))
    sleep 3
done
exec firefox-esr "$url"
LAUNCHER
chmod 755 /usr/local/bin/vmctl-open-proxmox
cat > /etc/xdg/autostart/vmctl-open-proxmox.desktop <<DESKTOP
[Desktop Entry]
Type=Application
Name=Proxmox VE web GUI
Comment=Open $url once the Proxmox lab host answers
Exec=/usr/local/bin/vmctl-open-proxmox
X-GNOME-Autostart-enabled=true
DESKTOP
