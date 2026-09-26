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

run=""
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *)
        run="./bin/"
        printf '\n  \033[33m%s/bin is not in your PATH yet\033[0m: open a new login shell, or run\n' "$PREFIX"
        printf '    source ~/.profile        (or add it: export PATH="%s/bin:$PATH" in ~/.zshrc / ~/.bashrc)\n' "$PREFIX"
        ;;
esac
printf '\n  next: \033[1m%svmctl web --open\033[0m (the lab in your browser)  or  \033[1m%svmtui\033[0m (terminal dashboard)\n' "$run" "$run"
printf '        \033[1m%svmctl bootstrap-preseed debian-server\033[0m installs a Debian VM with zero clicks\n' "$run"
