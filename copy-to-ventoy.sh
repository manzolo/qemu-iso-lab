#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Uso:
  sudo ./copy-to-ventoy.sh /dev/sdX1 [file.vhd]
  sudo ./copy-to-ventoy.sh /percorso/di/mount [file.vhd]

Esempi:
  sudo ./copy-to-ventoy.sh /dev/sdb1 cachyos-30g.vhd
  sudo ./copy-to-ventoy.sh /run/media/user/Ventoy cachyos-30g.vhd

Note:
  - Se passi una partizione blocco, lo script la monta sotto /mnt/ventoy-copy-$$
  - Se passi un mountpoint gia montato, copia direttamente li
  - Il file viene copiato con suffisso .vtoy, richiesto da Ventoy
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Comando mancante: $1" >&2
    exit 1
  }
}

cleanup() {
  if [[ "${MOUNTED_BY_SCRIPT:-0}" == "1" ]] && mountpoint -q "${TARGET_DIR}" 2>/dev/null; then
    umount "${TARGET_DIR}"
    rmdir "${TARGET_DIR}" 2>/dev/null || true
  fi
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

TARGET="${1:-}"
SRC_BASENAME="${2:-cachyos-30g.vhd}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_PATH="$DIR/$SRC_BASENAME"

if [[ -z "${TARGET}" ]]; then
  usage >&2
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Esegui come root: sudo $0 ..." >&2
  exit 1
fi

require_cmd cp
require_cmd sync
require_cmd mountpoint
require_cmd findmnt

if [[ ! -f "${SRC_PATH}" ]]; then
  echo "File sorgente non trovato: ${SRC_PATH}" >&2
  exit 1
fi

DEST_NAME="$(basename "${SRC_PATH}").vtoy"

MOUNTED_BY_SCRIPT=0
TARGET_DIR=""
trap cleanup EXIT

if [[ -b "${TARGET}" ]]; then
  require_cmd mount
  require_cmd umount
  TARGET_DIR="/mnt/ventoy-copy-$$"
  mkdir -p "${TARGET_DIR}"
  mount "${TARGET}" "${TARGET_DIR}"
  MOUNTED_BY_SCRIPT=1
elif [[ -d "${TARGET}" ]]; then
  TARGET_DIR="${TARGET}"
  if ! mountpoint -q "${TARGET_DIR}"; then
    echo "La directory esiste ma non risulta montata: ${TARGET_DIR}" >&2
    exit 1
  fi
else
  echo "Target non valido: ${TARGET}" >&2
  exit 1
fi

FSTYPE="$(findmnt -n -o FSTYPE --target "${TARGET_DIR}" || true)"
echo "Destinazione: ${TARGET_DIR} (${FSTYPE:-fs sconosciuto})"
echo "Copio: ${SRC_PATH}"
echo "Verso: ${TARGET_DIR}/${DEST_NAME}"

cp -f "${SRC_PATH}" "${TARGET_DIR}/${DEST_NAME}"
sync

echo
echo "Copia completata:"
echo "  ${TARGET_DIR}/${DEST_NAME}"
