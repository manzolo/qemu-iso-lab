# Common guide: host, vmctl and daily use

Everything that applies to every VM of the lab, to read once. The per-distro guides point
here for the repeated steps.

## 1. Host prerequisites (Linux)

```bash
# Ubuntu / Debian
sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 fzf xorriso virtiofsd p7zip-full
# Arch / CachyOS
sudo pacman -S qemu-desktop qemu-base edk2-ovmf python fzf xorriso virtiofsd p7zip

git clone git@github.com:manzolo/qemu-iso-lab.git && cd qemu-iso-lab
make install-cli          # symlinks vmctl and vmtui into ~/.local/bin
vmctl setup               # checks qemu, qemu-img, OVMF and the optional tools
```

`vmctl setup` must end with `Setup check passed`. The optional tools serve specific flows:
`xorriso` for the seed ISOs, `7z` for the Windows ISO (UDF file system), `virtiofsd` for
shared folders, `growisofs` and the Python `bcrypt` module for pfSense, `dialog`/`fzf` for
the TUI.

## 2. How an unattended bootstrap works

Every `vmctl bootstrap-*` command does the same things, only the installer changes:

1. Downloads the ISO into `isos/` (or uses the one the profile points at) and validates it.
2. Renders the profile's answer file and packs it into a small **seed ISO** under
   `artifacts/<vm>/`.
3. Extracts kernel and initrd from the ISO and starts QEMU **headless** with the serial
   console on stdio: everything the guest prints on `ttyS0`/`COM1` lands on your terminal and
   in `artifacts/<vm>/logs/bootstrap-serial.log`.
4. When needed, types on the serial by itself (`root` login, seed mount, script start).
5. Waits for the **completion token** (`==> ... installation complete!`) and lets the guest
   power itself off.
6. Restarts the installed VM in the background and runs the post-install over SSH on the
   profile's port.

While it runs you can watch the screen from another terminal:

```bash
vmctl attach <vm>            # opens remote-viewer on the VNC of the headless VM
vmctl attach <vm> --no-viewer   # only prints the vnc://127.0.0.1:PORT address
```

## 3. Customising without touching the tracked profiles

The profiles in `vms/profiles/*.json` are generic (user `lab`, password `lab`). Your own
settings go in `vms/profiles/local.json`, ignored by git:

```bash
make init-local-profile      # copies local.json.example
```

Example: change user, SSH key and shared folder of a profile.

```json
{
  "vms": {
    "arch-dms-local": {
      "archinstall_config": { "username": "YOUR_USER", "password": "YOUR_PASSWORD" },
      "ssh_provision": { "user": "YOUR_USER", "ssh_key": "~/.ssh/id_ed25519" },
      "shared_dir": { "source": "~/Workspaces/qemu/storage/shared", "tag": "shared" }
    }
  }
}
```

The user name must agree in every section that declares it; every `{{user}}` in the
profile's paths and commands follows. `vmctl show <vm> --json` prints the resolved profile.

## 4. Daily use

| What | Command |
|---|---|
| List and state | `vmctl list`, `vmctl status` |
| Start with a window | `vmctl start <vm>` |
| Start headless in the background | `vmctl start <vm> --headless --background` |
| Shell in the guest | `vmctl shell <vm>` (SSH with the key in `artifacts/<vm>/ssh/`) |
| Serial console of a background VM | `vmctl console <vm>` (`Ctrl-]` detaches) |
| Screen of a headless VM | `vmctl attach <vm>` |
| Clean shutdown | `vmctl stop <vm>` (ACPI button, then SSH, then SIGTERM) |
| Redo the post-install | `vmctl post-install <vm>` |
| Delete disk and artifacts | `vmctl clean <vm>` |
| Text menu | `vmtui` |

Plain SSH without `vmctl shell`:

```bash
ssh -i artifacts/<vm>/ssh/id_ed25519 -o BatchMode=yes -p <port> <user>@127.0.0.1
```

## 5. Shared folder (virtiofs)

A profile with `shared_dir` shares a host folder with the guest. At every start `vmctl`
launches a `virtiofsd` for the VM and adds the `vhost-user-fs-pci` device with the chosen tag.
On Linux guests the post-install writes `/mnt/<tag>` into fstab (systemd automount), mounts
it and links `~/<tag>` and `<Desktop>/<tag>`; on Windows guests it installs WinFSP, starts
`VirtioFsSvc`, the folder appears as a drive (usually `Z:`) with a shortcut on the desktop.

Manual mount on Linux, when needed: `sudo mount -t virtiofs <tag> /mnt/<tag>`.

## 6. Where to look when something stops

| File | Content |
|---|---|
| `artifacts/<vm>/logs/bootstrap-serial.log` | serial console of the installation |
| `artifacts/<vm>/logs/post-install.stdout.log` | output of the post-install commands |
| `artifacts/<vm>/logs/bootstrap-start.log` | stdout of the VM started in the background |
| `artifacts/<vm>/logs/serial.log` | serial console of a VM started in the background (`vmctl console`) |
| `artifacts/<vm>/logs/virtiofsd.log` | shared-folder daemon |
| `artifacts/<vm>/<flow>/` | generated answer files and scripts (preseed, ks.cfg, install.sh, autounattend.xml) |

Golden rule of every flow: the completion token is printed **after** `sync` and
`blockdev --flushbufs`, and the host waits for the guest to power itself off. If an install
looks successful but the first boot ends in `grub rescue>`, it is almost always this
sequence that was broken.
