#!/bin/sh
# First run on a fresh host: link vmctl and vmtui into ~/.local/bin, install every missing
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
printf '  linked %s/bin/vmctl and vmtui -> %s/bin\n' "$PREFIX" "$ROOT"

"$ROOT/bin/vmctl" setup --install "$@"

"$ROOT/bin/vmctl" welcome
