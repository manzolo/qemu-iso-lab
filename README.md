# QEMU ISO Lab

[![CI](https://github.com/manzolo/qemu-iso-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/manzolo/qemu-iso-lab/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab)
![Profiles](https://img.shields.io/badge/profiles-100%2B-84c9e7)
[![Catalog](https://img.shields.io/badge/catalog-browse%20online-7ddfc5)](https://manzolo.github.io/qemu-iso-lab/)
[![Tour](https://img.shields.io/badge/tour-8%20chapters%20%2B%2011%20lessons-7ddfc5)](https://manzolo.github.io/qemu-iso-lab/tour.html)

**Linux, BSD and Windows virtual machines on QEMU/KVM, installed with zero clicks.**
One JSON profile per VM describes the ISO (downloaded and checksummed), the disk, the
firmware, the unattended install and the SSH provisioning. Pick the profiles you want out
of the catalog and manage them from the **web dashboard** (`vmctl web`), the terminal
dashboard (`vmtui`) or the CLI (`vmctl`), all sharing the same profiles, VM state and jobs.
New here? **[The tour](https://manzolo.github.io/qemu-iso-lab/tour.html)** shows the whole road
in short clips (Italian voice, English and Italian subtitles), then one lesson per lab.

- **100+ profiles**, more than half of them fully unattended: every Ubuntu LTS since 8.04, Debian,
  Fedora, Arch, NixOS, openSUSE, FreeBSD, Haiku, ReactOS and Windows from 11 back to NT 4,
  plus hobby systems to boot and explore. Browse them, with a clip of each install, at
  [manzolo.github.io/qemu-iso-lab](https://manzolo.github.io/qemu-iso-lab/).
- **My VMs**: choose the profiles you care about; the dashboards open on them and nothing is
  downloaded until you install one. Every profile is versioned, with its changelog.
- **Labs**: groups of VMs on a private network, installed and started as one stack, each with
  a guide, its tests, a live network map and a packet inspector; or any two machines linked on the spot.
- **In your browser**: live VM screens, interactive SSH, file transfer, searchable commands, job
  logs and local profile customization, served on 127.0.0.1 (or your Wi‑Fi with `--lan`).
- **Tested for real**: every unattended profile is reinstalled from scratch by a local
  validation matrix.

> [!WARNING]
> **Use at your own risk.** QEMU ISO Lab is provided **as is**, without warranty of any kind
> ([MIT License](LICENSE)): you run it on your machine, with your data, under your responsibility.
> **`vmctl flash` and `vmctl import-device` write directly to physical disks.** `flash` overwrites
> the whole target device: everything on it is **erased and cannot be recovered**. Pick the device by
> its size, model and serial, check it twice, and keep a backup of anything you care about.
> Installs, `clean`, checkpoint restores and lab installs also delete or replace VM disks.

![The web dashboard: My VMs, two of them running, the selected one with its actions and facts](docs/screenshots/web-dashboard.png)

**Contents:** [Quick start](#quick-start) · [Gallery](#gallery) · [First VM](#from-zero-to-a-running-vm) · [The catalog](#the-catalog-pick-what-you-want) ·
[In the browser](#in-the-browser) · [Terminal dashboard](#the-terminal-dashboard) · [Labs](#labs) · [Everyday commands](#everyday-commands) ·
[Make it yours](#make-it-yours) · [Documentation](#documentation) · [Development](#development) · [License](#license)

## Quick start

**Linux**: paste this into a terminal (it needs `curl`; the script is [install.sh](install.sh)):

```sh
sh -c "$(curl -fsSL https://manzolo.github.io/qemu-iso-lab/install.sh)"
```

It clones the project into `~/qemu-iso-lab`, runs `setup.sh`, which lists the host tools it
would install and **asks before touching anything**, and adds **QEMU ISO Lab** to your
applications menu. About two minutes on a host that already has QEMU.

**Windows 11**: [download the installer](https://manzolo.github.io/qemu-iso-lab/install-windows.cmd)
and open it (an unsigned script: Edge asks to *Keep* it, Windows to **Run** it). vmctl needs
Linux, so it sets up **WSL2 + Ubuntu 24.04** in **three runs**, each ending by saying what to do
next: install WSL and restart, install Ubuntu and choose a user, clone and set up the lab. The
same from PowerShell: `irm https://manzolo.github.io/qemu-iso-lab/install.ps1 | iex`.

Then open **QEMU ISO Lab** from the applications menu (Linux) or the desktop (Windows): a
terminal window opens and the browser lands on the dashboard; closing the terminal stops the
dashboard, not the VMs. Updating is `vmctl update` (`--check` only lists what is new).
Already cloned, another distribution, the host packages by hand, WSL2 details:
**[docs/INSTALL.md](docs/INSTALL.md)**.

Or skip the dashboard and go straight to a VM, from an empty disk to a logged-in guest:

| You get | Command | Time |
|---------|---------|------|
| Debian server, over SSH | `vmctl bootstrap-preseed debian-server && vmctl shell debian-server` | ~5 min |
| Arch with niri + Noctalia | `vmctl bootstrap-archinstall arch-noctalia && vmctl start arch-noctalia` | ~7 min |
| Ubuntu 26.04 desktop | `vmctl bootstrap-unattended ubuntu-26.04 && vmctl start ubuntu-26.04` | ~20 min |
| Windows 11 (your ISO in `isos/`) | `vmctl bootstrap-windows windows-11 && vmctl start windows-11` | ~30 min |
| A three-node Proxmox cluster | `vmctl group install proxmox-lab` | ~25 min |

Times are from the last validation run on a desktop host with KVM.

## Gallery

<p align="center"><a href="docs/GALLERY.md"><img src="https://raw.githubusercontent.com/manzolo/qemu-iso-lab/media/gallery/gallery.gif" alt="Slideshow of the lab: catalog, dashboard, install job, console, labs, map, packet inspector, Windows, TUI" width="800"></a></p>

Frames from the tour's recordings. The thirteen captures, one by one and at full size:
**[docs/GALLERY.md](docs/GALLERY.md)**.

## From zero to a running VM

One complete lifecycle in screenshots, shot on a clean Lubuntu 22.04: clone, setup, then an
Ubuntu 26.04 desktop downloaded, installed unattended, used through the browser console and
SSH, checkpointed and stopped, all from the buttons of the web page; every step gives the
equivalent command.

**[Read the step-by-step guide](docs/FIRST_VM.md)** · [download it as a PDF](docs/FIRST_VM.pdf) (15 pages)

## The catalog: pick what you want

The catalog is large on purpose; you do not have to look at all of it. Browse it online at
**[manzolo.github.io/qemu-iso-lab](https://manzolo.github.io/qemu-iso-lab/)**: search, filter by
family, kind of install and role, watch each unattended profile install itself (a short clip of
a real install on every card), read its version and changelog, tick the ones you want and copy
the resulting line:

```bash
vmctl catalog add ubuntu-26.04 debian-server kali   # or right-click a row → Add to My VMs
vmctl catalog                                       # what is chosen; remove, set, clear
```

From then on both dashboards open on **My VMs** (your choice, plus whatever is running) and the
chosen profiles carry a ★. Nothing is downloaded until you install one.

[![The online catalog: cards per profile with the clip of its install, version, verification date and commands, three of them picked into a vmctl catalog add line](docs/screenshots/catalog-site.png)](https://manzolo.github.io/qemu-iso-lab/)

<details>
<summary><b>What it installs</b></summary>

| Family | Profiles | Unattended with |
|--------|----------|-----------------|
| Ubuntu and flavours | every LTS desktop from 8.04 to 26.04, Kubuntu, Xubuntu, Lubuntu, MATE, Budgie, Cinnamon, Studio | `bootstrap-unattended`, `bootstrap-preseed` |
| Debian, Kali | server, GNOME, KDE, Xfce, Kali | `bootstrap-preseed` |
| Fedora, RHEL | Workstation, KDE, Silverblue, Kinoite, AlmaLinux, Rocky, CentOS Stream | `bootstrap-kickstart` |
| Arch family | Arch with niri, DMS or Noctalia, CachyOS, Omarchy, pearOS, NVIDIA recipes | `bootstrap-archinstall`, `bootstrap-omarchy`, `bootstrap-pearos` |
| Linux Mint | 22.3 Cinnamon (Ubiquity in automatic mode) | `bootstrap-ubiquity` |
| More Linux | openSUSE Tumbleweed, NixOS, Alpine, cloud images | `bootstrap-autoyast`, `bootstrap-nixos`, `bootstrap-alpine`, `bootstrap-cloudimg` |
| BSD and others | FreeBSD, pfSense, ReactOS, Proxmox VE, Haiku | `bootstrap-freebsd`, `bootstrap-pfsense`, `bootstrap-reactos`, `bootstrap-proxmox`, `bootstrap-haiku` |
| Windows | 11 and 10 (OpenSSH, virtio drivers, shared folder), 7 | `bootstrap-windows` |
| Windows retro | XP, 2000, NT 4.0, 98 | `bootstrap-windowsxp`, `bootstrap-windows2000`, `bootstrap-windowsnt4`, `bootstrap-windows98` |

An unattended install runs headless on a serial console, boots the installed disk and runs the
profile's SSH provisioning. Every profile also has a manual install: `vmctl provision <vm>`
boots the ISO on a fresh disk. `vmctl` downloads and checksums every ISO that has a public
source; where there is none (Windows, the retro versions, pearOS), the dashboard's *ISO needed*
says exactly what to get and where to put it. Versions and history: [docs/PROFILES.md](docs/PROFILES.md#profile-versions).
</details>

## In the browser

```bash
vmctl web --open          # the dashboard on 127.0.0.1:8765 (make web does the same)
vmctl web --lan           # also from a phone or another PC on your network (HTTPS, self-signed: accept it once)
```

One page, served by Python's standard library; the URL printed at startup carries the token.

| From the dashboard | What you can do |
|--------------------|-----------------|
| **Profiles** | My VMs, Catalog and Labs; filters by state; right-click a row for its actions; Ctrl/Cmd+click or Shift+click to start, stop, link or unlink several VMs together. |
| **Live console** | The VM's screen with keyboard shortcuts, clipboard, screenshot, recording and auto-reconnect; **SSH** docked under it, **Files** to browse the guest, drag files in and download them, **Integration** for diagnostics and a confirmed guest command. Several consoles side by side on one page. |
| **Commands** | Every `vmctl` command as a form (F10): browse, filter, edit parameters, copy or run the preview. Destructive ones ask first. |
| **Machine details** | Console, SSH, start/stop, checkpoints, network, configuration, maintenance; **Customize** RAM, CPU or JSON in a local override. |
| **Labs** | Install or start a stack, run its tests, read its guide, open its consoles and its live network map. |
| **Recent activity** | Job logs live; jobs are shared with the TUI and keep running when you close the page. |

![The live browser console connected to the Ubuntu 26.04 desktop](docs/screenshots/web-console.png)

`/` searches, `F10` opens commands, **Ctrl/Cmd+Enter** runs a reviewed command. Physical-disk
**Flash** and **import-device** open in a terminal window on the host, where sudo asks for your
password. Everything else, shortcuts, local overrides, safety and the API: **[docs/WEB.md](docs/WEB.md)**.

## The terminal dashboard

`vmtui` lists every profile with its live state. Enter runs the suggested action for the
selected VM: install it when there is no disk, boot it when there is one, open its display
while it runs. The panel on the right has the other actions, and **All actions…** the full
menu; every action is a `vmctl` command, shown so you can copy it. `/` searches, **My VMs**,
**All**, **With disk**, **Running** and **Labs** (F2) filter; F8 or `vmtui --classic` gives the
fzf/dialog menus for terminals without Textual.

![vmtui: My VMs, the selected profile's facts and quick actions, installation activity](docs/screenshots/vmtui-dashboard.png)

Keys, video profiles and remote SPICE: [docs/VMTUI.md](docs/VMTUI.md).

## Labs

A lab is a group of VMs on a private network segment, installed and started as one stack with
`vmctl group install <lab>`. Each one comes with a guide in English and Italian, its own tests
that run inside the machines, a reset point, a live network map with a packet inspector (filters
in the Wireshark style: `tcp.port == 80`, `host 192.168.0.10`, `not arp`) and SSH, Console and
Manage under every machine of the map. The tour has one lesson per lab.

| Lab | What is in it |
|-----|---------------|
| `netlab` | pfSense router, Pi-hole DNS, Lubuntu desktop client |
| `proxmox-lab` | three Proxmox VE nodes clustered over ZFS mirrors, a Debian client with a browser |
| `k8s-lab` | a three-node MicroK8s cluster |
| `docker-lab` | images, containers, volumes and Compose |
| `git-lab` | commits, branches, merge, conflicts, stash, rebase and git flow |
| `mysql-lab` | tables, queries, transactions, users, backups and phpMyAdmin |
| `ssh-lab` | hardening a server and watching it defend itself, from a client |
| `vpn-lab` | WireGuard and OpenVPN between two machines |
| `lvm-lab`, `mdadm-lab`, `zfs-lab` | volumes and snapshots, software RAID with a failed disk and a hot spare, a RAIDZ pool and a scrub |

![Web Labs view: the two labs ready to install and a session lab of two linked machines](docs/screenshots/web-labs.png)

Any VMs can also be put on the same private network on the spot, without a profile:
`vmctl link kali debian-server` (or a machine's network button in the dashboard, click or
drag). A running VM gets a hot-plugged NIC, a stopped one gets it at its next start, Linux,
FreeBSD and Windows guests are given their address, and the pair appears under **Labs** as a
*session* lab with its own map until `vmctl link --off`. Members, commands, maps and the
inspector's filter language: **[docs/LABS.md](docs/LABS.md)**.

## Everyday commands

| I want to... | Run |
|--------------|-----|
| choose my profiles | `vmctl catalog add <vm> [<vm>...]`, `vmctl catalog` |
| see the VMs and their state | `vmctl list`, `vmctl status`, `vmctl show <vm>` |
| boot, enter, stop | `vmctl start <vm>`, `vmctl shell <vm>`, `vmctl stop <vm>` |
| try something and throw it away | `vmctl start <vm> --ephemeral --headless --background` (web: *Start ephemeral*) |
| watch a headless VM, even mid-install | `vmctl attach <vm>` (screen), `vmctl console <vm>` (serial) |
| record an install as a time-lapse | `vmctl record <vm>` while a bootstrap runs (GIF, `--mp4` for the video) |
| keep a copy to go back to | `vmctl checkpoint create <vm> clean`, later `restore` ([guide](docs/CHECKPOINTS.md)) |
| make an independent second VM | `vmctl clone <vm> <new-name>` ([guide](docs/CLONE.md)) |
| connect two VMs | `vmctl link <vm> <vm>` ([guide](docs/LABS.md)) |
| open a VM in virt-manager | `vmctl export-libvirt <vm>` ([guide](docs/LIBVIRT.md)) |
| write a VM to a USB disk, or import one | `vmctl flash`, `vmctl import-device` ([guide](docs/IMPORT_DISKS.md)) |
| free space | `vmctl clean <vm>`, `vmctl delete-iso <vm>`, `vmctl clean-reports` |
| update the lab | `vmctl update` |

Every command accepts `--dry-run`, and `vmctl --help` lists them all by task.
Tab completion: `echo 'eval "$(vmctl completion zsh)"' >> ~/.zshrc` (bash works too).

## Make it yours

Tracked profiles use a generic guest user, `lab` with password `lab`. The first `vmctl web`
asks for your identity; the dashboard's **Customize** sets RAM, CPU or any JSON field of one
VM. Both write `vms/profiles/local.json`, which git ignores and which is merged over every
profile (your My VMs selection lives there too), and which you can also edit by hand:

```bash
make init-local-profile && $EDITOR vms/profiles/local.json
```

Details: [docs/PROVISIONING.md](docs/PROVISIONING.md).

## Documentation

| Page | Read it for |
|------|-------------|
| [Catalog site](https://manzolo.github.io/qemu-iso-lab/) | every profile, its version and history, what to run |
| [Tour](https://manzolo.github.io/qemu-iso-lab/tour.html) ([how it is made](docs/TOUR.md)) | eight short chapters and the lab lessons, Italian voice, English and Italian subtitles |
| [docs/README.md](docs/README.md) | the map of every page, in reading order |
| [INSTALL](docs/INSTALL.md) | installing by hand, host packages per distribution, Windows and WSL2, updating |
| [FIRST_VM](docs/FIRST_VM.md) ([PDF](docs/FIRST_VM.pdf)) | from `git clone` to a running Ubuntu desktop, every screen of the lifecycle |
| [GALLERY](docs/GALLERY.md) | thirteen captures of the lab's screens |
| [WEB](docs/WEB.md) | the lab in a browser: actions, jobs, API, safety |
| [VMTUI](docs/VMTUI.md) | the terminal dashboard and the classic menus |
| [LABS](docs/LABS.md) | every lab, the live map, the packet inspector, temporary links |
| [PROFILES](docs/PROFILES.md) | the profile model, versions, and how to add a VM |
| [UNATTENDED](docs/UNATTENDED.md) | how each unattended install works, and the validation matrix |
| [PROVISIONING](docs/PROVISIONING.md) | your identity, packages and dotfiles in a fresh guest |
| [guides/](docs/guides/README.md) | printable step-by-step guides in English and Italian (`make guides`) |

## Development

```bash
make check          # mypy --strict + every test (the dashboard's too), before each push
make site           # the catalog site into site/ (GitHub Pages publishes it on push)
make validate-vms   # local only: reinstall every unattended profile, HTML report (hours)
make help           # every developer target
```

The `Makefile` holds developer targets only; anything a user runs is a `vmctl` subcommand.
Tests never touch the host, and CI runs them on Python 3.10 to 3.14. After editing a tracked
profile's recipe, `tools/bump_profile.py <vm> patch|minor|major -m "..."` records the new
version. More in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## License

[MIT](LICENSE). The software is provided "as is", without warranty of any kind; the authors are not
liable for any damage or data loss arising from its use. Read the warning at the top before using
`vmctl flash` or `vmctl import-device`.
