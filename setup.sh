#!/bin/sh
# First run: link vmctl, vmtui and qemu-iso-lab into ~/.local/bin, install every missing
# dependency (asks first; Textual goes into .venv-tui without sudo), then check the host.
# Needs only sh and python3: a fresh Ubuntu has no make, so this is the Quick Start entry
# point and `make setup` just calls it. Extra arguments go to `vmctl setup --install`
# (tool names, or --yes to skip the question).
set -eu
ROOT=$(cd "$(dirname "$0")" && pwd)
PREFIX=${PREFIX:-$HOME/.local}

if ! command -v python3 >/dev/null 2>&1; then
    echo "setup: python3 is required (Debian/Ubuntu: sudo apt install python3; Arch: sudo pacman -S python)" >&2
    exit 1
fi

mkdir -p "$PREFIX/bin"
ln -sfn "$ROOT/bin/vmctl" "$PREFIX/bin/vmctl"
ln -sfn "$ROOT/bin/vmtui" "$PREFIX/bin/vmtui"
ln -sfn "$ROOT/bin/qemu-iso-lab" "$PREFIX/bin/qemu-iso-lab"
printf '  linked vmctl, vmtui and qemu-iso-lab in %s/bin -> %s/bin\n' "$PREFIX" "$ROOT"

"$ROOT/bin/vmctl" setup --install "$@"

python3 "$ROOT/tools/install_launchers.py" "$ROOT"
"$ROOT/bin/vmctl" welcome
printf '\nStart QEMU ISO Lab from your applications menu, or run:\n  "%s/bin/qemu-iso-lab"\n' "$PREFIX"
