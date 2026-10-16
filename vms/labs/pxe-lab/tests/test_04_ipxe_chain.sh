#!/usr/bin/env bash
# Exercise 4 — iPXE gets a script over HTTP, the script chains pxelinux over TFTP, the installer was told BOOTIF.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../../_common.sh"
echo ""; echo "${BOLD}Exercise 4 — from the firmware to the installer${RESET}"; echo ""

script=$(on pxe-lab-client curl -s http://172.20.9.1/boot.ipxe)
assert_contains "boot.ipxe is an iPXE script" "$script" "^#!ipxe"
assert_contains "it chains pxelinux over TFTP" "$script" "chain tftp://172\.20\.9\.1/lpxelinux\.0"
assert_contains "it sets pxelinux's path prefix (option 210)" "$script" "set 210:string tftp://172\.20\.9\.1/"
assert_contains "a UEFI client falls back to the installer itself" "$script" "kernel http://172\.20\.9\.1/debian/linux"
size=$(on pxe-lab-client "curl -sI http://172.20.9.1/debian/initrd.gz | tr -d '\\r' | awk 'tolower(\$1)==\"content-length:\" {print \$2}'")
assert "the initrd is served over HTTP (${size:-0} bytes)" test "${size:-0}" -gt 10000000
# grep in the guest: the installer's syslog is large, and a grep -q quitting early on a host pipe
# ends it with SIGPIPE, which pipefail counts as a failure.
assert "the installer was booted with BOOTIF" on pxe-lab-client sudo grep -q "BOOTIF=01-" /var/log/installer/syslog
assert "and fetched its answers from the server" on pxe-lab-client sudo grep -qF "url=http://172.20.9.1/debian/preseed.cfg" /var/log/installer/syslog

report_results "Exercise 4"
