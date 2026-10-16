#!/usr/bin/env bash
# Exercise 2 — the TFTP root holds a small boot loader, its modules and the menu; the client fetches the menu over TFTP.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 2 — what TFTP serves${RESET}"; echo ""

for file in lpxelinux.0 ldlinux.c32 menu.c32 libutil.c32 libcom32.c32 pxelinux.cfg/default; do
    assert "$file is in the TFTP root" on pxe-lab-server test -s /srv/pxe/tftp/$file
done
assert "lpxelinux.0 is the packaged loader" on pxe-lab-server cmp -s /srv/pxe/tftp/lpxelinux.0 /usr/lib/PXELINUX/lpxelinux.0
assert "ldlinux.c32 matches it (same syslinux)" on pxe-lab-server cmp -s /srv/pxe/tftp/ldlinux.c32 /usr/lib/syslinux/modules/bios/ldlinux.c32
assert "nothing over 32 MB in the TFTP root: big files go over HTTP" on pxe-lab-server sh -c '[ -z "$(find /srv/pxe/tftp -type f -size +32M)" ]'
menu=$(on pxe-lab-server cat /srv/pxe/tftp/pxelinux.cfg/default)
assert_contains "the kernel comes over HTTP" "$menu" "KERNEL http://172\.20\.9\.1/debian/linux"
assert_contains "the initrd comes over HTTP" "$menu" "INITRD http://172\.20\.9\.1/debian/initrd\.gz"
assert_contains "BOOTIF is passed to the installer" "$menu" "IPAPPEND 2"
assert_contains "a local-disk entry exists" "$menu" "LOCALBOOT 0"
fetched=$(on pxe-lab-client curl -s tftp://172.20.9.1/pxelinux.cfg/default)
assert_contains "the client fetches the menu over TFTP" "$fetched" "MENU TITLE pxe-lab"
log=$(on pxe-lab-server sudo journalctl -u dnsmasq --since -5min --no-pager)
assert_contains "dnsmasq-tftp logged the transfer" "$log" "sent /srv/pxe/tftp/pxelinux\.cfg/default to 172\.20\.9\."

report_results "Exercise 2"
