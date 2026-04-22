#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

qemu-system-x86_64 \
  -enable-kvm \
  -m 4096 \
  -cpu host \
  -smp 4 \
  -machine q35,accel=kvm \
  -boot menu=on \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file="$DIR/OVMF_VARS_4M.fd" \
  -drive file="$DIR/cachyos-30g.vhd",format=vpc,if=virtio \
  -vga std \
  -display gtk \
  -device ich9-intel-hda \
  -device hda-duplex \
  -netdev user,id=n1 \
  -device virtio-net-pci,netdev=n1
