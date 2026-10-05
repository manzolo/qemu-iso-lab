#!/bin/sh
# Download first, then execute with stdin still connected to the terminal:
# sh -c "$(curl -fsSL https://manzolo.github.io/qemu-iso-lab/install.sh)"
set -eu

main() {
    if [ "$(uname -s)" != Linux ]; then
        echo 'This installer needs Linux. On Windows, use the PowerShell installer.' >&2
        exit 1
    fi

    install_dir=${VMCTL_INSTALL_DIR:-$HOME/qemu-iso-lab}
    repo=https://github.com/manzolo/qemu-iso-lab.git
    case "$install_dir" in
        /*) ;;
        *) echo 'VMCTL_INSTALL_DIR must be an absolute path.' >&2; exit 1 ;;
    esac

    # setup.sh handles QEMU and the other host tools; bootstrap only what it needs.
    packages=
    command -v git >/dev/null 2>&1 || packages="$packages git"
    if ! command -v python3 >/dev/null 2>&1; then
        if command -v pacman >/dev/null 2>&1; then
            packages="$packages python"
        else
            packages="$packages python3"
        fi
    fi
    if [ -n "$packages" ]; then
        echo "Installing prerequisites:$packages"
        as_root=
        if [ "$(id -u)" != 0 ]; then
            command -v sudo >/dev/null 2>&1 || {
                echo "Install these packages as administrator, then retry:$packages" >&2
                exit 1
            }
            as_root=sudo
        fi
        if command -v apt-get >/dev/null 2>&1; then
            $as_root apt-get update
            $as_root apt-get install -y $packages
        elif command -v pacman >/dev/null 2>&1; then
            $as_root pacman -S --needed --noconfirm $packages
        elif command -v dnf >/dev/null 2>&1; then
            $as_root dnf install -y $packages
        elif command -v zypper >/dev/null 2>&1; then
            $as_root zypper --non-interactive install $packages
        else
            echo "Install these packages with your package manager, then retry:$packages" >&2
            exit 1
        fi
    fi
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10 or newer is required.")'

    if [ -e "$install_dir" ]; then
        # Re-running setup must not overwrite another directory or update local work. A checkout
        # cloned by hand over SSH (git@github.com:...) is the same project.
        origin=
        [ -d "$install_dir/.git" ] && origin=$(git -C "$install_dir" remote get-url origin 2>/dev/null || true)
        case "$origin" in
            "$repo"|*github.com[:/]manzolo/qemu-iso-lab|*github.com[:/]manzolo/qemu-iso-lab.git) same_project=1 ;;
            *) same_project= ;;
        esac
        if [ -z "$same_project" ] || [ ! -f "$install_dir/setup.sh" ]; then
            echo "Cannot reuse $install_dir: it is not a QEMU ISO Lab checkout." >&2
            echo 'Choose another directory with VMCTL_INSTALL_DIR.' >&2
            exit 1
        fi
        echo "Using existing checkout: $install_dir (no automatic git pull)"
    else
        git clone "$repo" "$install_dir"
    fi
    cd "$install_dir"
    ./setup.sh "$@"
    # setup.sh itself ends with how to start the lab; only an existing checkout that predates the
    # launcher (left untouched: no automatic pull) needs the older command spelled out.
    if [ ! -x ./bin/qemu-iso-lab ]; then
        printf '\nOpen the dashboard:\n  "%s/bin/vmctl" web --open\n' "$install_dir"
        echo 'Update your checkout and rerun setup.sh to get the application-menu launcher.'
    fi
}

main "$@"
