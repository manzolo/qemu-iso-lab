#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISO_NAME="${ISO_NAME:-cachyos-desktop-linux-260308.iso}"
VHD_NAME="${VHD_NAME:-cachyos-30g.vhd}"
VHD_SIZE="${VHD_SIZE:-30G}"
OVMF_CODE="${OVMF_CODE:-/usr/share/OVMF/OVMF_CODE_4M.fd}"
OVMF_VARS_SRC="${OVMF_VARS_SRC:-/usr/share/OVMF/OVMF_VARS_4M.fd}"
OVMF_VARS_DST="$DIR/OVMF_VARS_4M.fd"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Comando mancante: $1" >&2
    exit 1
  }
}

require_file() {
  [[ -f "$1" ]] || {
    echo "File mancante: $1" >&2
    exit 1
  }
}

require_cmd qemu-img
require_cmd qemu-system-x86_64
require_cmd cp
require_cmd chmod

require_file "$DIR/$ISO_NAME"
require_file "$OVMF_CODE"
require_file "$OVMF_VARS_SRC"

if [[ -e "$DIR/$VHD_NAME" ]]; then
  echo "Esiste gia: $DIR/$VHD_NAME" >&2
  exit 1
fi

qemu-img create -f vpc -o subformat=fixed "$DIR/$VHD_NAME" "$VHD_SIZE"
cp "$OVMF_VARS_SRC" "$OVMF_VARS_DST"

cat >"$DIR/run-install.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail

DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"

qemu-system-x86_64 \\
  -enable-kvm \\
  -m 4096 \\
  -cpu host \\
  -smp 4 \\
  -machine q35,accel=kvm \\
  -boot menu=on \\
  -drive if=pflash,format=raw,readonly=on,file=$OVMF_CODE \\
  -drive if=pflash,format=raw,file="\$DIR/OVMF_VARS_4M.fd" \\
  -drive file="\$DIR/$VHD_NAME",format=vpc,if=virtio \\
  -cdrom "\$DIR/$ISO_NAME" \\
  -vga std \\
  -display gtk \\
  -device ich9-intel-hda \\
  -device hda-duplex \\
  -netdev user,id=n1 \\
  -device virtio-net-pci,netdev=n1
EOF

cat >"$DIR/run-boot.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail

DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"

qemu-system-x86_64 \\
  -enable-kvm \\
  -m 4096 \\
  -cpu host \\
  -smp 4 \\
  -machine q35,accel=kvm \\
  -boot menu=on \\
  -drive if=pflash,format=raw,readonly=on,file=$OVMF_CODE \\
  -drive if=pflash,format=raw,file="\$DIR/OVMF_VARS_4M.fd" \\
  -drive file="\$DIR/$VHD_NAME",format=vpc,if=virtio \\
  -vga std \\
  -display gtk \\
  -device ich9-intel-hda \\
  -device hda-duplex \\
  -netdev user,id=n1 \\
  -device virtio-net-pci,netdev=n1
EOF

chmod +x "$DIR/setup-vhd.sh" "$DIR/run-install.sh" "$DIR/run-boot.sh"

echo "Preparazione completata:"
echo "  ISO : $DIR/$ISO_NAME"
echo "  VHD : $DIR/$VHD_NAME"
echo "  UEFI: $OVMF_VARS_DST"
echo
echo "Installa con: $DIR/run-install.sh"
echo "Avvia il disco con: $DIR/run-boot.sh"
