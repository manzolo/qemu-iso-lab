#!/usr/bin/env bash
set -euo pipefail

VERSION="${VTOYBOOT_VERSION:-1.0.36}"
TAG="v${VERSION}"
ISO_NAME="vtoyboot-${VERSION}.iso"
ISO_URL="https://github.com/ventoy/vtoyboot/releases/download/${TAG}/${ISO_NAME}"
WORKDIR="${WORKDIR:-/tmp/vtoyboot-${VERSION}}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Comando mancante: $1" >&2
    exit 1
  }
}

cleanup() {
  if mountpoint -q "${WORKDIR}/mnt" 2>/dev/null; then
    umount "${WORKDIR}/mnt"
  fi
}

trap cleanup EXIT

if [[ "${EUID}" -ne 0 ]]; then
  echo "Esegui questo script come root: sudo $0" >&2
  exit 1
fi

require_cmd curl
require_cmd find
require_cmd tar

mkdir -p "${WORKDIR}"
rm -rf "${WORKDIR}/extract" "${WORKDIR}/mnt"
mkdir -p "${WORKDIR}/extract" "${WORKDIR}/mnt"

echo "Scarico ${ISO_URL}"
curl -L --fail --output "${WORKDIR}/${ISO_NAME}" "${ISO_URL}"

INNER_TAR=""

if command -v bsdtar >/dev/null 2>&1; then
  echo "Estraggo la ISO con bsdtar"
  bsdtar -C "${WORKDIR}/extract" -xf "${WORKDIR}/${ISO_NAME}"
else
  require_cmd mount
  require_cmd cp
  echo "Monto la ISO in loop e copio il contenuto"
  mount -o loop,ro "${WORKDIR}/${ISO_NAME}" "${WORKDIR}/mnt"
  cp -a "${WORKDIR}/mnt/." "${WORKDIR}/extract/"
  umount "${WORKDIR}/mnt"
fi

INNER_TAR="$(find "${WORKDIR}/extract" -maxdepth 2 -type f -name 'vtoyboot-*.tar.gz' | head -n1)"

if [[ -z "${INNER_TAR}" ]]; then
  echo "Archivio interno vtoyboot-*.tar.gz non trovato in ${WORKDIR}/extract" >&2
  exit 1
fi

echo "Estraggo $(basename "${INNER_TAR}")"
tar -C "${WORKDIR}/extract" -xzf "${INNER_TAR}"

SCRIPT_PATH="$(find "${WORKDIR}/extract" -maxdepth 3 -type f -name 'vtoyboot.sh' | head -n1)"

if [[ -z "${SCRIPT_PATH}" ]]; then
  echo "Script vtoyboot.sh non trovato dopo l'estrazione" >&2
  exit 1
fi

echo "Eseguo ${SCRIPT_PATH}"
(
  cd "$(dirname "${SCRIPT_PATH}")"
  sh "./$(basename "${SCRIPT_PATH}")"
)

echo
echo "Completato. Ora spegni la VM e rinomina il disco in qualcosa come:"
echo "  cachyos-30g.vhd.vtoy"
echo "Poi copialo sulla partizione dati della chiavetta Ventoy."
