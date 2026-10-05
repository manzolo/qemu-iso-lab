# Installing QEMU ISO Lab

The README's [Quick start](../README.md#quick-start) is the short version: one line on Linux,
one installer on Windows. This page has everything behind it: what the installers do, how to
install by hand, the host packages per distribution, the Windows details and how to update.

## What the one-line installer does (Linux)

```sh
sh -c "$(curl -fsSL https://manzolo.github.io/qemu-iso-lab/install.sh)"
```

[install.sh](../install.sh), published by GitHub Pages next to the catalog:

1. installs `git` and `python3` if missing, with `apt`, `pacman`, `dnf` or `zypper` (sudo asks);
2. clones the project into `~/qemu-iso-lab` (`VMCTL_INSTALL_DIR=/other/path` installs elsewhere);
   a rerun **reuses** an existing checkout and never pulls or touches local changes;
3. runs `./setup.sh`, which lists the host tools it would install and **asks before touching
   anything**, links `vmctl`, `vmtui` and `qemu-iso-lab` into `~/.local/bin`, writes the
   **QEMU ISO Lab** entry of the applications menu (`tools/install_launchers.py`, with the
   project's icon) and ends with `vmctl welcome`, the list of first commands.

About two minutes on a host that already has QEMU. No VM image is downloaded until you choose one.

## Already cloned the repository

```bash
git clone https://github.com/manzolo/qemu-iso-lab.git && cd qemu-iso-lab
./setup.sh        # links vmctl + vmtui into ~/.local/bin, installs what is missing, checks the host
vmctl web --open  # the browser dashboard: pick a profile and install or boot it
```

No `make` needed (a fresh Ubuntu has none): `make web` and `make setup` are aliases.
If `~/.local/bin` is not on your PATH yet, run `./bin/vmctl web --open`. Prefer a terminal?
`vmtui` is the TUI, `vmctl` the CLI. The dashboard prints a URL with a token; open that URL
if the browser does not open by itself.

`./setup.sh` needs only `python3`, which every supported distribution ships. It uses `apt` or
`pacman` for QEMU, OVMF and the helpers, and puts Textual (the terminal dashboard) in the
repository's `.venv-tui` without sudo. Running it again only installs what is missing;
`vmctl setup` alone checks (`-v`: one line per tool), `vmctl setup --install [names]` installs.

## Host packages by hand

For other distributions, or to see exactly what `./setup.sh` would run:

```bash
# Arch / CachyOS
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python openssh libvirt make dialog fzf cloud-image-utils xorriso virtiofsd virt-viewer p7zip dvd+rw-tools python-bcrypt swtpm gdisk ddrescue partclone util-linux
# Debian / Ubuntu (Ubuntu 22.04 has no virtiofsd package: ./setup.sh fetches the upstream build)
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 python3-venv openssh-client libvirt-clients libvirt-daemon-system make dialog fzf cloud-image-utils xorriso virtiofsd virt-viewer p7zip-full dvd+rw-tools python3-bcrypt swtpm gdisk gddrescue partclone fdisk

make install textual    # only the dashboard, into .venv-tui (or name any tool: make install xorriso)
vmctl setup             # check again (-v: one line per tool)
```

Only `qemu-system-x86_64`, `qemu-img`, `python3` (3.10 or newer) and the OVMF firmware are
required; every other tool serves one flow, and `vmctl setup` says which. Without Textual,
`vmtui` opens the classic fzf/dialog menus. KVM needs hardware virtualization enabled in the
firmware and your user in the `kvm` group (setup tells you).

## Windows 11

vmctl needs Linux (KVM, `/proc`, Unix sockets), so on Windows it runs inside a **WSL2**
distribution, which gets `/dev/kvm` through nested virtualization.

[Download the installer](https://manzolo.github.io/qemu-iso-lab/install-windows.cmd)
([install-windows.cmd](../install-windows.cmd)) and open it. It is an unsigned script: the browser
asks whether to keep it (Edge: *Keep*, then *Open file*) and Windows warns once more, choose
**Run** (*Cancel* is the default button). It downloads [setup-windows.ps1](../setup-windows.ps1)
and runs it; the whole setup takes **three runs**, each one ending by saying what to do next:

1. installs WSL (accept the administrator prompt), then **restart Windows**;
2. installs Ubuntu 24.04 and asks you to choose a Linux user name and password;
3. clones the project, runs `setup.sh` in Ubuntu (asks before installing, then wants that
   password for `sudo`), turns nested virtualization on in `.wslconfig`, adds you to the `kvm`
   group and puts **QEMU ISO Lab** on the desktop and in the Start menu.

The same from PowerShell:

```powershell
irm https://manzolo.github.io/qemu-iso-lab/install.ps1 | iex
```

or with the script downloaded by hand:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1       # run it again after the reboot it asks for
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1 -Web  # the dashboard, opened in the Windows browser
```

By hand it is `wsl --install -d Ubuntu-24.04`, then the Linux one-liner in the Ubuntu shell.
WSL forwards localhost, so the dashboard works in the Windows browser at the printed
`http://127.0.0.1:8765/?token=…` URL; windows of *Boot with display* appear through WSLg.
Flashing or importing a physical disk needs the disk attached to WSL first (`wsl --mount`,
from an administrator PowerShell). This setup has not been through the validation matrix yet:
reports are welcome.

## Opening the lab

When setup finishes, open **QEMU ISO Lab** from the applications menu (Linux) or the desktop
(Windows): a terminal window opens and the browser lands on the dashboard. Keep that terminal
open while you use the lab; closing it (or Ctrl-C) stops the dashboard, not the VMs. On Linux
the command `qemu-iso-lab` does the same (`~/qemu-iso-lab/bin/qemu-iso-lab` while `~/.local/bin`
is not on your PATH). `vmctl web --lan` serves the same page to a phone or another PC on your
network (HTTPS with a self-signed certificate, accepted once; the URL carries the key).

## Updating

One command, on Linux and inside the Windows Ubuntu alike:

```sh
vmctl update          # fetch the latest release, fast-forward, relink, install what a new release needs (asks first)
vmctl update --check  # only list what is new
```

It refuses to run over local edits to tracked files (your `local.json` is not one), so nothing is
lost; a dashboard already open keeps the previous code until you start it again. Running the
installer again is safe too: on Linux it reuses the checkout without pulling, on Windows it
continues where it stopped.
