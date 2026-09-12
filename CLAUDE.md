# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Developer loop (the Makefile has ONLY these kinds of targets: `make help`)
make check                                     # mypy --strict + full pytest suite; run before every commit and push
make test                                      # pytest only
python -m pytest tests/test_archinstall.py -v  # single test file
python -m pytest tests/ -k "test_render"       # filter by name
make ci                                        # python -m unittest discover -s tests -v (what GitHub Actions runs)
make lint                                      # python -m mypy vmctl/ --strict (enforced)
make install-cli                               # symlink vmctl + vmtui into ~/.local/bin
make guides                                    # docs/guides/{it,en} -> pdf/<lang>/qemu-iso-lab-guide.pdf (manual) + pdf/<lang>/single|singole/ (markdown + weasyprint); keep both languages in sync
make validate-vms                              # LOCAL ONLY (hours): check-vms --restore --report over every unattended profile, opens the HTML report

# VM lifecycle: ONE front door, the vmctl CLI (./bin/vmctl if not installed).
vmctl --help                       # commands grouped by task + typical flows: read this first
vmctl <command> --help             # options of one command
vmctl list --names                 # profile names, one per line
vmctl show <name> --json           # resolved profile ({{user}} already expanded)
vmctl --dry-run <command> <name>   # every command supports --dry-run
vmctl bootstrap-archinstall arch-noctalia
vmctl bootstrap-preseed debian-server
vmctl bootstrap-kickstart almalinux-server
vmctl bootstrap-alpine alpine-niri
vmctl bootstrap-windows windows11-unattended   # needs isos/windows11.iso (no public URL) + 7z
vmctl bootstrap-pfsense pfsense-lab            # needs isos/pfSense-CE-2.7.2-RELEASE-amd64.iso (no public URL) + growisofs + python bcrypt
vmctl bootstrap-reactos reactos                # needs isos/ReactOS-0.4.16-i386.iso (SourceForge ships a zip) + xorriso; install only
vmctl lab plan|install [--export]|up|down|status|check|clean   # the network lab (pfSense + Pi-hole + client) on plain QEMU
vmctl lab export|unexport|libvirt-test         # the same lab handed to libvirt (lab-lan network) and back
vmctl export-libvirt <name>                    # hand an installed VM (and the lab-lan network) to libvirt
vmctl attach <name>                # VNC view of a headless VM, also while a bootstrap runs
vmctl console <name>               # serial console (ttyS0 login / pfSense menu) of a background VM, Ctrl-] detaches
```

Before pushing, run the relevant local tests first. Do not use GitHub Actions as the first place to discover breakage in unit tests, dry-run bootstrap flows, or CI wiring. At minimum, if you touch CI or unattended/bootstrap code, run `python -m unittest discover -s tests -v` and any focused bootstrap/dry-run commands affected by the change.

## Architecture

`bin/vmctl` is a 12-line shim that calls `vmctl.cli:main`; `make install-cli` symlinks it (and `vmtui`) into `~/.local/bin`, and both resolve the repository root through the symlink. The `Makefile` carries developer targets only and must not grow VM-lifecycle targets again: new user-facing behaviour goes into a `vmctl` subcommand, registered in `cli.py` inside one of the `COMMAND_GROUPS` (a test fails otherwise). `bin/vmtui` is an independent menu wrapper (fzf backend, `dialog` fallback, `VMTUI_UI` to force one) that shells out to `vmctl`.

### Module import order (no cycles allowed)

```
errors ← state ← {ui, runtime} ← {config, iso, cloud_init, qemu, archinstall, disk_inspect}
      ← {alpine, autoyast, preseed, kickstart, omarchy, windows} ← {flash, import_dev, ssh, host_setup, report} ← netlab ← {pfsense, libvirt} ← lifecycle ← cli
```

Mutable globals (`ROOT`, `CONFIG_DIR`, etc.) live in `state.py` and are always accessed as `state.ROOT`, never imported directly — a direct import captures a stale binding and breaks tests.

### Key modules

| Module | Role |
|---|---|
| `cli.py` | Argument parser + `main()`. Wires subcommands to handlers. |
| `lifecycle.py` | All `cmd_*` handlers not involving flash/import. Background-VM PID tracking. |
| `config.py` | `load_config()` merges all `vms/profiles/*.json`. `get_vm()` resolves deprecated names through `PROFILE_ALIASES`; local override keys use the same aliases. |
| `qemu.py` | Builds `qemu-system-x86_64` argument lists. `common_args()` is the central builder. `run_and_expect()` drives headless serial-console automation. |
| `cloud_init.py` | Renders cloud-init `user-data`/`meta-data` and Ubuntu `autoinstall` seed ISOs. |
| `archinstall.py` | Arch-specific: renders archinstall JSON config (interactive) or a self-contained `pacstrap`-based `install.sh` (automated bootstrap). |
| `iso.py` | ISO download with validation, discovery regex, and member extraction (`xorriso`/`bsdtar`). |
| `alpine.py` | Alpine: `setup-alpine` answer file + chroot `install.sh` packed into a seed ISO, live-prompt automation constants. |
| `autoyast.py` | openSUSE: AutoYaST XML profile + chroot script packed into an `AUTOINST` seed CD, DVD loader kernel/initrd, SATA CD-ROM args. |
| `windows.py` | Windows 7/10/11: `autounattend.xml` + first-logon `vmctl-setup.ps1` in a `VMCTLSEED` CD, prompt-free ISO rebuild (`7z` + `xorriso`), virtio-win ISO, SATA CD-ROM args. |
| `kickstart.py` / `preseed.py` | AlmaLinux/Rocky/Fedora kickstart and Debian preseed rendering; `kickstart.install_repo()` picks `cdrom` or a netinst URL; `kickstart.ostree_config()`/`resolve_ostree_ref()` switch the flow to `ostreesetup` for Silverblue. |
| `libvirt.py` | Render persistent libvirt XML and define/undefine existing disks; `export-libvirt` / `unexport-libvirt` handlers in lifecycle enforce running-VM checks. |
| `ssh.py` (cont.) | `reboot_guest()` asks the guest to reboot and tolerates the dropped connection (exit 255); `lifecycle.run_verify_after_reboot()` then waits for SSH and runs `ssh_provision.verify_after_reboot`. Assertions about a desktop installed *by* the post-install belong there: greetd restarted mid-install does not keep its session (verified live). |
| `report.py` | `check-vms --report [DIR] --open`: self-contained HTML with profile status and historical live verification, per-worker JSON and QMP P6→PNG screenshots before stop, outside restored artifacts. `wake_console()` sends one `send-key` before the final screendump, or a server guest past the console-blanking timeout is captured as a black rectangle; the install-time watcher captures with `wake=False` so nothing types into a running installer. |
| `netlab.py` | Network lab: `network_lab` topology resolution/validation, netplan + `pihole.toml` + guest `setup.sh` rendering, SSH post-install hook (`provision_guest`), libvirt segment network XML, `vmctl lab` helpers. |
| `reactos.py` | ReactOS: `unattend.inf` render (both Setup stages, `[GuiRunOnce]` ending in the guest's shutdown), per-VM BootCD rebuild with xorriso (`-boot_image any replay`, answer file under `/i386` and `/reactos`, `freeldr.ini` defaulting to Setup), IDE CD-ROM args, profile checks. |
| `pfsense.py` | pfSense CE 2.7.2 scripted install: `config.xml` render (bcrypt users, WAN admin rules, NAT forwards), per-VM ISO rebuild (`xorriso` extract + `growisofs -M` graft), `rc.local`/`installerconfig`, CD-ROM args, profile checks. |
| `ssh.py` | SSH/SCP helpers, `wait_for_ssh`, `post_install_copy`, `post_install_run`. |

### Profile model

All VM definitions live in `vms/profiles/*.json`. `load_config()` reads and merges every file in that directory; `vms/profiles/local.json` is gitignored, loaded last, and deep-merged over the tracked profiles (dicts merge, lists concatenate). It is the only place for personal data — copy from `local.json.example`.

Tracked profiles are generic on purpose: the guest user is `lab` (password `lab`, hash included) and every place where the user name appears inside a path, a command or a file body writes `{{user}}`. `config.expand_user_placeholder()` replaces it at load time with the identity declared by the profile (`ssh_provision.user`, `cloud_init.user`, `autoinstall.username`, `archinstall_config.username`, `preseed_config.username`, `kickstart_config.username`, `alpine_config.username`, `windows_config.username`; they must agree). Overriding the identity in `local.json` therefore propagates everywhere. Never commit a real user name, password or hash into a tracked profile again; the repo is public.

SSH-provisioned ports in use: `cachyos-desktop` → 2223, `cachyos-nvidia` → 2224, `arch-noctalia` → 2226, `arch-dms` → 2230, `arch-dms-nvidia` → 2231, `arch-omarchy-nvidia` → 2232, `fedora-niri-dms` → 2233, `alpine-niri` → 2234, `windows11-unattended` → 2235, `windows10-unattended` → 2236, `pfsense-lab` → 2237, `pihole-lab` → 2238, `lubuntu-lab` → 2239 (the last two are also NAT forwards on the router's WAN, see `network_lab`), `lubuntu-24.04` → 2240, `kubuntu-24.04` → 2241, `xubuntu-24.04` → 2242, `ubuntu-mate-24.04` → 2243, `ubuntu-budgie-24.04` → 2244, `rocky-9` → 2245, `fedora-silverblue` → 2246, `opensuse-tumbleweed-autoyast` → 2247, `ubuntu-gnome-24.04` → 2248, `ubuntu-8.04-unattended` → 2252, `ubuntu-10.04-unattended` → 2253, `ubuntu-12.04-unattended` → 2254, `ubuntu-14.04-unattended` → 2255, `ubuntu-16.04-unattended` → 2256, `ubuntu-18.04-unattended` → 2257, `ubuntu-20.04-unattended` → 2258, `ubuntu-22.04-unattended` → 2259 (a test in `test_repo_profiles.py` fails if two tracked profiles share a port) (2222 is `ubuntu-niri`; 2228/2229/2290 are Debian/Alma/Ubuntu; 2225 is taken by a local.json VM).

### Unattended install flows

**Ubuntu** (`bootstrap-unattended`):
1. Generates cloud-init seed ISO + autoinstall seed.
2. Extracts `casper/vmlinuz` + `casper/initrd` from the ISO, boots with `-append autoinstall -no-reboot`.
3. Waits for installer to exit, then starts the installed VM headless in background.
4. Runs `ssh_provision` / `cloud_init.post_install_run` over SSH.

**Arch** (`bootstrap-archinstall`):
1. Generates a self-contained `install.sh` (sgdisk → pacstrap → arch-chroot → GRUB) packed into a `bootstrap.iso`.
2. Extracts `arch/boot/x86_64/vmlinuz-linux` + `initramfs-linux.img` from the Arch live ISO.
3. Boots headless with serial stdio (`console=ttyS0,115200`) and `archisobasedir=arch archisolabel=ARCH_YYYYMM`.
4. Uses `run_and_expect` + `auto_inputs` to wait for `root@archiso` on the serial console, then sends the mount + run trigger automatically.
5. Waits for `"==> Arch Linux installation complete!"`, then repeats step 3–4 of the Ubuntu flow.

CachyOS (`cachyos-desktop`, `cachyos-nvidia`) rides the same handler on the CachyOS archiso: `installer_boot` selects `vmlinuz-linux-cachyos`/`initramfs-linux-cachyos.img`, `archinstall_config.live_login_prompt`/`live_shell_prompt` (`CachyOS login:` / `root@CachyOS`) replace the archiso prompts, `live_kernel_append` adds `systemd.unit=multi-user.target`, and `inherit_live_pacman_conf` copies the live `pacman.conf` + mirrorlists into the target after pacstrap (the `[cachyos]` repo would otherwise be lost). `archinstall.live_prompts()` / `live_kernel_append()` are the only places reading these fields.

**Debian** (`bootstrap-preseed`):
1. Generates preseed seed ISO (`PRESEED_CFG`).
2. Extracts `vmlinuz` + `initrd.gz` from the ISO.
3. Boots headless with serial stdio and appropriate preseed kernel appends.
4. Uses `run_and_expect` to wait for `"==> Debian preseed install complete!"`, then starts installed VM headless.

**Ubuntu desktop history** (`ubuntu-8.04/10.04/12.04/14.04/16.04/18.04-unattended`, `vms/profiles/ubuntu-lts.json`) rides `bootstrap-preseed` on the d-i media (alternate CDs, the 14.04 server ISO; `installer_boot` = `install/vmlinuz` + `install/initrd.gz`). What the old guests needed, all verified live on 2026-09-12: `preseed_config.disk_device: "auto"` (8.04's installer has both the old IDE drivers and libata, so partman picks the only disk and GRUB goes to `(hd0)`), `upgrade: none` + `extra` (empty `apt-setup/services-select`: old-releases has no security pocket on the vendor host), the NOPASSWD rule also as the *last* line of `/etc/sudoers` (8.04 has no `#includedir`, 10.04 lists `%admin` after it), `UseDNS no` in sshd_config (5 s reverse lookup per login, longer than the old 5 s SSH probe, now 15 s), APT periodic disabled (`/etc/cron.daily/apt` is a shell script whose comm is `apt` and sleeps up to 30 min; the package wait now matches `apt` by command line), `ssh_provision.key_type: rsa` + `ssh_options` (`HostKeyAlgorithms`/`PubkeyAcceptedAlgorithms` `+ssh-rsa`, `KexAlgorithms +diffie-hellman-group-exchange-sha256`) for sshd 4.7/5.3/5.9, GDM 2.x / LightDM autologin and the `ttyS0` getty via `late_commands`, and a desktop check on `pgrep -x 'gnome-session|x-session-manag(er)?'` because `verify-desktop` needs systemd and `pgrep -x` sees the 15-character comm. From 16.04 the guests run systemd: shared `verify-desktop`, `serial-getty@ttyS0.service`, GDM3 autologin on 18.04. `vmport: false` on 14.04+ (the vmmouse X driver otherwise captures the absolute pointer and the desktop mouse stands still, verified). `ubuntu-20.04-unattended` and `ubuntu-22.04-unattended` are `bootstrap-unattended` copies of `ubuntu-gnome-24.04` (20.04's subiquity 22.07 resolves autoinstall `packages` against the CD pool only, the mirror waits in sources.list.curtin.old: `ubuntu-desktop` is 'Unable to locate' there, verified live, so it goes through `autoinstall.late_commands` with `curtin in-target`) (live-server autoinstall + `ubuntu-desktop`, gdm3 autologin drop-in), which is the 24.04 entry of the series. The server media do not install the `ubuntu-desktop` *task*: the metapackage goes through `pkgsel/include`. Ubuntu 8.04 needs `disk.interface: "ide"` + `e1000` (kernel 2.6.24, no virtio); `reactos` (manual, `audio_device: ac97`) uses the same interface.

**openSUSE** (`bootstrap-autoyast`, `autoyast_config`): renders an AutoYaST XML profile (partitioning, patterns, services) whose chroot script creates the user, sudoers drop-in, SSH key, GDM autologin, `sshd` and a `ttyS0` getty, then `sync` → `blockdev --flushbufs` → `==> AutoYaST install complete!`. The seed is an `AUTOINST` image on a **USB stick** (`usb-storage`) and the installer ISO is the only SATA `ide-cd` (`autoyast.install_media_args`): linuxrc scans `/dev/sr*` for the repository, so the install medium must be a CD, but a *second* CD makes YaST stop halfway through the package install on "Insert 'cd-<id>' (Disc 1)" because it cannot tell which drive is the repo (verified live, screenshot in the report). Kernel/initrd come from `boot/x86_64/loader/` and the append is `install=<repo> autoyast=usb:///autoinst.xml ifcfg=*=dhcp netsetup=dhcp textmode=1 console=ttyS0,115200`; `autoyast_config.install_repo` is required by the NET image, which carries no packages. `second_stage` is false by default: the YaST second stage would otherwise run on tty1 at first boot and, after an install whose reboot QEMU swallowed, greet the user with "The previous installation has failed. Would you like it to continue?" (verified live), so the chroot script owns `systemctl enable` for `enable_services`, the default target, the display manager and `firewall-offline-cmd --add-service` for `open_firewall_services` (openSUSE opens only dhcpv6-client, so a forwarded SSH port hangs at the banner). `final_reboot` stays true and QEMU runs with `-no-reboot`, so the guest's own reboot ends the install.

**Fedora Silverblue** rides `bootstrap-kickstart`: `kickstart_config.ostree` replaces the `%packages` section with one `ostreesetup` line and `autopart` drops `--type=plain`. `kickstart.resolve_ostree_ref()` reads the ref from `refs/heads` inside the ISO (xorriso) and falls back to `ostree.ref` on a dry run or when the read fails, so a new Fedora release only needs a new `iso`/`iso_url`. `%post` can configure but not install: extra packages are layered in the post-install with `rpm-ostree install` and take effect at the next boot.

**Ubuntu desktop flavors** (`lubuntu-24.04`, `kubuntu-24.04`, `xubuntu-24.04`, `ubuntu-mate-24.04`, `ubuntu-budgie-24.04`) are `bootstrap-unattended` on the plain Ubuntu Server ISO: the flavor is the metapackage in `autoinstall.packages`, plus a cloud-init autologin drop-in for the display manager it ships (SDDM for Lubuntu/Kubuntu, LightDM for the others) and a `serial-getty@ttyS0`. No new code: a sixth flavor is a profile.

**AlmaLinux/RHEL/Fedora** (`bootstrap-kickstart`; `kickstart_config.inst_repo` = `cdrom` or a netinst repository URL, `ignore_missing_packages` → `%packages --ignoremissing`):
1. Generates kickstart seed ISO (`KS_CFG`).
2. Extracts `vmlinuz` + `initrd.img` from the ISO.
3. Boots headless with serial stdio and appropriate kickstart kernel appends.
4. Uses `run_and_expect` to wait for `"==> Kickstart install complete!"`, then starts installed VM headless.

**Alpine** (`bootstrap-alpine`):
1. Generates a `setup-alpine` answer file + `install.sh` (setup-alpine with `ERASE_DISKS`, then chroot: packages, password hash, sudo, `chroot_commands`) into an `ALPINESEED` seed ISO on a virtio CD-ROM.
2. Extracts `boot/vmlinuz-<flavor>` + `boot/initramfs-<flavor>` (`lts` for the standard ISO).
3. Boots with the ISO's `modules=` list plus `console=ttyS0,115200`; `auto_inputs` answer `localhost login:` with `root` and type the mount + run trigger at `localhost:~#`.
4. Waits for `"==> Alpine Linux installation complete!"`, then the usual background start + post-install.

**Windows 10/11** (`bootstrap-windows`, the kvm-lab `autounattend` technique on plain QEMU):
1. Rebuilds the Microsoft ISO once as `isos/<stem>-noprompt.iso` (`7z x` because the files live in UDF only, then `xorriso -as mkisofs` with `efi/microsoft/boot/efisys_noprompt.bin` as the UEFI El Torito image and an emptied `boot/bootfix.bin`): no "Press any key to boot from CD or DVD". Profile-independent, so it is cached forever.
2. Renders `autounattend.xml` + `vmctl-setup.ps1` into a `VMCTLSEED` seed ISO. Windows Setup searches the root of every removable drive for the answer file, so the 6 GB ISO never changes with the profile.
3. Boots headless with serial stdio (= COM1 in the guest), **without** `-no-reboot` (Setup reboots several times), three SATA CD-ROMs pinned to `ide.0`/`ide.1`/`ide.2` (install ISO with `bootindex=2`, virtio-win, seed) and the disk as `virtio-blk-pci,bootindex=1` (`common_args(disk_bootindex=1)`): OVMF falls through to the CD only while the disk is empty. The answer file injects `viostor`/`NetKVM` from the virtio-win CD in WinPE (every drive letter D..G), wipes disk 0 (GPT EFI/MSR/Windows), bypasses the TPM/Secure Boot/CPU/RAM checks via `HKLM\SYSTEM\Setup\LabConfig`, creates the local administrator with autologon. Nothing runs in specialize: a non-zero `RunSynchronousCommand` blocks Setup with a modal dialog (verified), and `EnableLUA=0` there leaves the Windows 11 OOBE black (verified). FirstLogonCommands is ONE short `cmd.exe /c for %d in (D E F G) do if exist %d:\vmctl-setup.ps1 powershell ... -File %d:\vmctl-setup.ps1` line (`windows.first_logon_command`, asserted < `RUNONCE_MAX_COMMAND_LENGTH` = 260): Setup stores it as an HKLM `RunOnce` value and **Windows 10 silently skips RunOnce values longer than 260 chars** (verified in-guest: 307-char test entry ignored, short one executed; the old 294-char PowerShell one-liner never ran on Win10 while Win11 ran it). The `<OOBE>` block uses only the Hide*/ProtectYourPC flags plus `Microsoft-Windows-International-Core` in oobeSystem (without the deprecated `SkipMachineOOBE`/`SkipUserOOBE`, Win10 otherwise stops at the region/keyboard pages). Do not reintroduce Skip*OOBE, scheduled tasks or UAC toggles.
4. At first logon `vmctl-setup.ps1` runs as the local administrator (`setup_commands` may target that user's HKCU), which installs the virtio guest tools, OpenSSH Server (`Add-WindowsCapability`, retried; project public key in `administrators_authorized_keys` with the strict ACL), disables sleep/hibernation, runs the profile's PowerShell `setup_commands`, logs every step to `C:\vmctl\setup.log` **and** to COM1, then writes `"==> Windows installation complete!"` on COM1 and runs `shutdown /s`. Every step is an `Invoke-Step` under `$ErrorActionPreference = 'Stop'` with exit-code checks; after any failure the script writes `"==> Windows installation FAILED: <steps>"` instead and still shuts down, so the host fails fast (`cmd_bootstrap_windows` rewrites the VMError). The prompt-free ISO cache carries a `.source` stamp (path+size+mtime) and is rebuilt when the source changes. `edition` must match the image name in `install.wim` (`Windows 11 Pro` on Microsoft ISOs, `Windows 11 Professional` on UUP dump builds; `image_index` is the alternative), generic keys resolve by family. `run_and_expect(..., exit_grace_sec=windows.SHUTDOWN_GRACE_SEC)` waits up to 10 minutes for that shutdown (the guest's own shutdown is the flush here).
5. `vmctl stop` honours `acpi_poweroff_grace_sec` (300 in the Windows profiles: the first shutdown commits the OpenSSH feature operation and exceeds the default 60 s; a SIGTERM there would corrupt the guest) and its SSH fallback is `shutdown /s /t 0 /f` for `windows_config` profiles.
6. Starts the installed VM headless and runs `run_windows_post_install`: `wait_for_ssh(probe_command="exit 0")`, `copy_from_host` as plain `scp -r`, `post_install_run` through cmd.exe (no `sh -lc`; keep commands locale-independent, e.g. PowerShell one-liners rather than `findstr` on localized `systeminfo` output).

**pfSense / network lab** (`bootstrap-pfsense`, `vmctl lab`): `vms/profiles/network-lab.json` = `pfsense-lab` (router, `pfsense_config` + `network_lab.role=pfsense` with the LAN topology), `pihole-lab` and `lubuntu-lab` (Ubuntu 22.04.5 autoinstall members with `network_lab.gateway_vm`). NICs come from the profile's `networks` list (`qemu.network_specs`/`network_args`; no `networks` = the historical single slirp NIC, byte-identical args): `type` `user` (slirp, `hostfwd`) or `segment` (multicast socket netdev derived from the segment name; libvirt network after export), `phase` `install`/`runtime`/`both`. Every bootstrap handler and `start_installed_vm_headless` pass `network_phase="install"`; `vmctl start` uses runtime. The router renders the whole `config.xml` (`pfsense.render_config_xml`, bcrypt via the optional `bcrypt` module), builds `artifacts/<vm>/pfsense/install.iso` by grafting `installerconfig`/`rc.local`/patched `bsdinstall/script` into a copy of the local ISO with `growisofs -M` (an xorriso rebuild loses FreeBSD's hidden El Torito extents), boots BIOS/`pc` with disk `bootindex=1` + IDE CD `bootindex=2`, and waits for `==> pfSense installation complete!` (rc.local: `sync` → token → `shutdown -p now`; the FAILED token also powers off). Linux members: `netlab.provision_guest` runs inside `run_post_install` (after `provision_shared_dir`), detects the guest NIC name from the MAC, uploads `artifacts/<vm>/netlab/` to `/tmp/vmctl-netlab` and runs `setup.sh` (Pi-hole `basic-install.sh --unattended` with pre-seeded `pihole.toml`; static netplan for the next boot; cloud-init network disabled; client: wait-online disabled). Host access on plain QEMU is only through the router's WAN forwards (8080 GUI, 8081 Pi-hole, 2238/2239 SSH) which `netlab.topology` cross-checks against the NAT rules. `libvirt.ensure_segment_networks` defines/starts `lab-lan` on export.

**ReactOS** (`bootstrap-reactos`, `reactos_config`, verified live 2026-09-12 in ~90 s): Setup reads `unattend.inf` from `\I386` (not the LiveCD copy under `\reactos`, the first mistake); the BootCD menu defaults to the Live environment, so the rebuilt ISO carries `freeldr.ini` with `DefaultOS=Setup`. No `-no-reboot` (two Setup reboots), disk `bootindex=1`, CD `ide-cd` on `ide.1`. The completion token is the kernel's `Entering sleep state S5` line on COM1: the release kernel owns that port for its debug log, so a user-mode `echo > COM1` from GuiRunOnce never appears (verified), while the `shutdown.exe /s /f /t 5` that ends `[GuiRunOnce]` produces the ACPI line exactly once. Install only, no SSH; `check_profile` insists on bios/pc/ide/1 CPU.

**Windows 7** (`windows7-unattended`, kvm-lab's Windows7U): `windows.is_legacy_windows()` (edition `Windows 7 ...` or `driver_flavor` w7) switches `render_autounattend` to BIOS/MBR (System Reserved + Windows, install partition 2), no LabConfig, `w7` driver paths, `Skip*OOBE`+`NetworkLocation`, and a specialize command calling `vmctl-cert.cmd` (certutil Root + TrustedPublisher, always exit 0). First logon is not elevated under UAC; `render_legacy_setup_script` stays PowerShell 2.0, and `install_openssh` is always False on 7. `ensure_noprompt_iso(legacy=True)` deletes `boot/bootfix.bin` (an emptied one hangs `etfsboot.com`) and passes `-D -N -d` to xorriso (`CDBOOT` looks up `BOOTMGR`, not `BOOTMGR.;1`), both verified live; the `.source` stamp records both. `local_test_mode` maps a Windows profile without `ssh_provision` to install-only `bootstrap-windows`. Generic keys: Ultimate/Enterprise `33PXH-...`, Professional `FJ82H-...`; `edition_family` knows `Ultimate`.

For legacy profiles declaring `guest_agent: true`, `windows.installs_guest_agent()` injects `vioserial` alongside `viostor`/`NetKVM` in WinPE. The host downloads the separate `windows_config.guest_agent_msi` (`path`, `url`, required `sha256`) and carries its exact bytes on the seed as `vmctl-qga.msi`; the tracked Windows 7 profile pins Fedora's `qemu-ga-win-101.1.0-1.el7ev` (2020). Its SHA-256 was measured from the HTTPS archive MSI on 2026-09-07 (2,228,736 bytes); it is a repository content pin, not a vendor-published checksum. Specialize Order 2 calls `vmctl-qga.cmd stage`, copying the MSI and script to `C:\Windows\Setup\Scripts`. `SetupComplete.cmd` then runs the normal `msiexec /i /qn /norestart` as SYSTEM after Setup, when WMI is available. Both branches exit 0. First logon checks `C:\vmctl-qga-exit.txt` (0 or 3010) and that `QEMU-GA` is running inside `Invoke-Step`; failure uses the existing FAILED token and natural shutdown, never a modal specialize error. Logs: `C:\vmctl-qga.log`, `C:\vmctl-qga-msi.log`, `C:\vmctl\setup.log`. Windows 10/11 retain their answer files and first-logon guest tools installer unchanged.

**Windows 7 agent diagnosis (verified on RTM 6.1.7600, no SP1):** the agent 110.0.2 on virtio-win 0.1.285 fails in the Windows loader with missing `api-ms-win-core-path-l1-1-0.dll`, before reaching main, opening the channel or creating the requested verbose console log. This explains StartServices 1920/rollback 1603 and SCM 7009/7000 (1053); retries and manual service registration cannot fix it. The missing child INF is a false lead: `VIOSerialPort` is a raw PDO (`WdfPdoInitAssignRawDevice` in the upstream vioserial driver), and the existing driver exposes `\\.\Global\org.qemu.guest_agent.0`. Live `QueryDosDevice` resolved it and `CreateFile` succeeded; agent 101.1.0 then answered ping, OS info and addresses on the same driver, and its normal MSI install returned 0 with `QEMU-GA` running. A clean bootstrap with the pinned MSI also passed: the service was running before the token, the guest shut down naturally, and ping/OS info/addresses worked at the subsequent disk boot. MSI registration in specialize is still too early for WMI/VSS, so keep SetupComplete. Do not replace the compatible agent with the current ISO MSI or require a child INF. The unrelated failed USB xHCI driver is `VEN_1B36&DEV_000D`. Driver signature compatibility on other Win7 media still needs its own validation; this result does not promise SHA-2 support on RTM. Always `vmctl clean windows7-unattended` before testing bootstrap: an existing Windows disk boots ahead of the installer CD.

`shared_dir` (`{"source", "tag"}`, in `qemu.py`): every `common_args()` call for such a VM starts a per-VM `virtiofsd --sandbox none` on `artifacts/<vm>/runtime/virtiofs-<tag>.sock` (`ensure_virtiofsd`, waits for the socket, daemon exits with QEMU), switches the RAM to `memory-backend-memfd,share=on` + `-machine memory-backend=mem0` and adds `vhost-user-fs-pci`. Windows profiles then install WinFSP (`windows_config.winfsp_url`), start `VirtioFsSvc`, wait for the new drive letter and drop a `<tag>.lnk` on the desktop in the first-logon script; on Linux guests `ssh.provision_shared_dir` (called by `run_post_install`) writes the fstab automount entry for `/mnt/<tag>`, mounts it and links `~/<tag>` plus the desktop folder. `vmctl setup` looks for `virtiofsd` in `/usr/libexec` too and for `7z`/`7zz`/`7za`.

Prerequisites: the retail ISO at the profile's `iso` path (Microsoft has no stable URL; `local.json` may point at an existing file), `7z`, `xorriso`. `windows_config.password` is plain text (the answer file cannot take a hash); `edition`/`language` must exist in the ISO.

The interactive variant (`install-archinstall`) generates archinstall JSON configs and attaches them as a second virtio CD-ROM (`/dev/vdb`) for the user to run manually.

#### CRITICAL — completion token / ESP flush invariant (do not break)

The Arch unattended bootstrap has a sequencing rule that cost 5+ hours of debugging when violated. This applies to **all** automated unattended flows (Arch, Debian, RHEL) that signal completion via serial. **Treat this as gospel:**

1. In the install script (or `%post`/`late_command`) it MUST end with this exact ordering:
   ```bash
   sync
   blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true
   echo "==> Arch Linux installation complete!"   # BOOTSTRAP_COMPLETE_TOKEN
   poweroff -f
   ```
   The completion token must appear **after** the sync+flush, never before. If the token is printed first, the host kills QEMU before the guest finishes flushing the ESP, so `EFI/BOOT/BOOTX64.EFI`, the embedded GRUB binary, and the stub `grub.cfg` files end up partially or entirely missing. Symptom: guest drops to `grub rescue>` on first real boot, and an ESP inspection shows only `EFI/GRUB/grubx64.efi` from `grub-install`.

2. In `vmctl/qemu.py::run_and_expect`, when `expected_text` matches, the function MUST call `process.wait(timeout=30)` and let the guest power itself off naturally. Only fall back to `terminate()` → `kill()` if that wait times out. Do NOT replace this with an immediate `terminate()` on token match — that re-introduces the same race.

If a future change appears to need either of these relaxed (e.g. "speed up the bootstrap by killing QEMU sooner", "drop the redundant sync"), the answer is no — investigate the actual problem elsewhere. Any new unattended flow that signals completion via a serial token must follow the same flush → token → natural-poweroff pattern.

### Testing conventions

`tests/_common.py` exports `BaseVmctlTestCase` with a temp-dir root and a `_VmctlFacade` that flattens all vmctl submodules into a single attribute namespace. When adding a new module, add its import to both the `import` block and the `_SEARCH_ORDER` tuple in `_common.py`.

Tests mock `vmctl.runtime.run` (for subprocess calls) and `shutil.which` (for tool detection). Patch the submodule directly (`mock.patch.object(vmctl.runtime, "run")`), not through the facade.

#### Host isolation (a test must pass identically on the developer's host and in the bare CI runner)

The CI `test` job has no QEMU, no `qemu-img`, no `fzf`, no ISOs and no installed VMs; the developer's host has all of them. A test that reads any of that passes only on one side. Rules:

- **Never resolve paths against the real checkout.** Relative profile paths (`isos/`, `artifacts/`, PID files under `artifacts/<vm>/runtime/`) resolve against `state.ROOT`, so tests must point it at a temp dir (`BaseVmctlTestCase` does; `test_vmtui.py` passes a temp `VMTUI_ROOT_DIR` with `vmctl/` and `bin/` symlinked from the checkout). Simulate disk state by writing files there: `mark_installed` (allocated data) / `mark_prepared` (sparse file, no `qemu-img`).
- **Never load `vms/profiles/local.json`.** It is the developer's personal, gitignored file. Copy the tracked profiles into a temp config dir and skip it; a test-specific `local.json` in that temp dir is fine.
- **Build subprocess environments from scratch**, not with `os.environ.copy()`: minimal PATH (temp tools dir, `Path(sys.executable).parent`, `os.defpath`), temp `HOME`, `LC_ALL=C.UTF-8`, explicit `VMTUI_UI=dialog`. Fake external tools (`dialog`, `fzf`) as tiny scripts in the temp tools dir.
- **No external binaries in the `test` job.** Do not call `qemu-img`, `xorriso`, `bsdtar`, `virsh` or `qemu-system-*` in unit tests; mock `vmctl.runtime.run` or fake the artifact.
- Before pushing, run the suite once with those tools hidden from PATH (e.g. a dir of symlinks to `/usr/bin/*` minus `qemu-*`, `fzf`, `dialog`, `xorriso`, `bsdtar`) to reproduce the CI runner locally.

### Artifact layout

```
artifacts/<vm>/
├── disk.qcow2 (or .vhd)
├── OVMF_VARS.fd
├── installer/          # extracted kernel/initrd for unattended install
├── autoinstall/        # Ubuntu autoinstall seed
├── cloud-init/         # cloud-init seed
├── archinstall/        # Arch config ISO / bootstrap script
├── logs/
└── runtime/            # PID files, qmp.sock (graceful stop), vnc.sock (`vmctl attach`) and serial.sock (`vmctl console`, logged to logs/serial.log) of background VMs
```

### CI

GitHub Actions includes these baseline jobs: `test` (unittest), `boot-smoke` (`boot-check alpine-ci` under TCG/QEMU, no KVM), and `ubuntu-niri-dry-run` (`--dry-run` of the full bootstrap). The `alpine-ci` profile is the stable CI guest — keep it small and TCG-capable.

CI is a confirmation step, not the first feedback loop. If you modify workflow files, bootstrap handlers, or unattended install helpers, make the corresponding local `unittest` and dry-run checks pass before pushing.

### Profile names and migration

Names follow `<distro>[-<version>][-<variant>]`. Include a version only when versions coexist. Variants describe sessions (gnome, kde, xfce, niri, dms, noctalia, omarchy), roles (server, minimal, router, client), installation styles (live, netinst, installer), platforms (bios, efi, nvidia, gl) or purposes (ci, template, lab). The explicit compatibility map also retains desktop and installed. Existing Ubuntu flavor, Windows and openSUSE Tumbleweed names are unchanged. Shared profiles never acquire a `-local` suffix.

`tools/migrate_profile_names.py` previews artifact directory moves; `--apply` performs atomic no-overwrite renames. It refuses conflicting destinations and active runtime PIDs. Stop VMs and use the renamed checkout before applying it. `--local-config PATH` also backs up and rewrites local override keys and path components; `--local-only` skips artifact moves. Keep backup files private and untracked. Runtime files and already generated guest seeds are not rewritten; regenerate seeds using the canonical profile before another installation. SSH ports are unchanged.

Alpine CI media is pinned to Virt 3.24.1 (`core.json`, versioned URL and SHA-256, no discovery). Updating it is an explicit maintenance change; keep both CI profiles on the same verified ISO.

Pinned ISO SHA-256 provenance is in `docs/ISO_CHECKSUMS.md`. `tools/verify_profile_isos.py --iso-root PATH` audits existing cache files without downloading, changing or deleting them; mismatches must never be resolved by replacing vendor hashes with local hashes. Dynamic discovery profiles need a matching per-release checksum mechanism before static hashes can safely be added.

Desktop post-install checks use the shared guest script `vms/profile-files/common/bin/verify-desktop`. Never accept an enabled display manager as proof of autologin: require a local, active, non-greeter graphical session for the expected user and installed packages, with bounded startup retries. OpenRC Alpine checks greetd plus the user-owned niri process. The package-only Ubuntu niri recipes do not promise autologin.

### Profile status and live verification

Every tracked profile declares `meta.status` (`manual`, `unattended`, `experimental`). `meta.verified`, when present, is the last maintainer-supplied live PASS date, not the date of the last edit or unit test. The canonical list and JSON output expose status/verified; the HTML report stores profile_status/profile_verified separately from each run's status. Do not promote experimental profiles or advance dates from a dry run. See `docs/PROFILE_TODO.md` for definitions, historical dates and remaining work.
