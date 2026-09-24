#!/bin/sh
# pve-repos.sh: the lab has no subscription, so the enterprise repositories (which answer 401
# and make "Update package database" fail in the GUI) are disabled and pve-no-subscription added.
set -eu
. /etc/os-release
for file in /etc/apt/sources.list.d/pve-enterprise.sources /etc/apt/sources.list.d/ceph.sources; do
    [ -f "$file" ] || continue
    grep -q '^Enabled:' "$file" || printf 'Enabled: no\n' >> "$file"
    sed -i 's/^Enabled:.*/Enabled: no/' "$file"
done
cat > /etc/apt/sources.list.d/proxmox.sources <<SOURCES
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: $VERSION_CODENAME
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
SOURCES
apt-get update
