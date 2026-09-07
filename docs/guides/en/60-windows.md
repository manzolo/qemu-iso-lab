# Unattended Windows 11 and Windows 10

Profiles: `windows11-unattended` (SSH 2235) and `windows10-unattended` (SSH 2236). The same
technique as kvm-lab (`autounattend.xml`) ported to plain QEMU. Typical time: 25-40 minutes.

## 1. Specific prerequisites

- The Microsoft ISO. There is no stable URL: download it from the browser
  (https://www.microsoft.com/en-us/software-download/windows11, "ISO multi-edition x64") and
  put it at `isos/windows11.iso`, or point `iso` at it in `local.json`. Windows 10 works the
  same with `isos/windows10.iso`.
- `7z` and `xorriso` on the host (`vmctl setup` lists them): the Microsoft ISO keeps its files
  in UDF and is rebuilt once without the "Press any key to boot from CD" prompt.
- The virtio-win driver ISO: fetched from fedorapeople at first use into
  `isos/virtio-win.iso`, or `windows_config.virtio_iso` pointing at a local copy.
- `virtiofsd` if you want the shared folder (the profiles already have it).

## 2. Customisation in local.json

```json
"windows11-unattended": {
  "iso": "/path/Win11_25H2_Italian_x64_v2.iso",
  "windows_config": {
    "username": "YOUR_USER", "password": "YOUR_PASSWORD", "realname": "Name Surname",
    "edition": "Windows 11 Pro", "language": "it-IT", "input_locale": "it-IT",
    "timezone": "W. Europe Standard Time",
    "virtio_iso": "/path/virtio-win-0.1.285.iso"
  },
  "ssh_provision": { "user": "YOUR_USER" },
  "shared_dir": { "source": "/path/shared", "tag": "shared" }
}
```

`edition` must be the image name inside `install.wim` ("Windows 11 Pro" on Microsoft ISOs,
"Windows 10 Pro" for 10). The password is plain text: the answer file takes no hash.

## 3. The command

```bash
vmctl bootstrap-windows windows11-unattended        # --timeout 3600 by default
vmctl attach windows11-unattended                   # in another terminal, to watch
```

## 4. What happens, step by step

1. **Prompt-free ISO** (once): `7z x` unpacks the ISO, `xorriso -as mkisofs` rebuilds it with
   `efi/microsoft/boot/efisys_noprompt.bin` as the UEFI boot image. The result is
   `isos/<name>-noprompt.iso`, with a `.source` file remembering which ISO it came from.
2. **Seed** `VMCTLSEED` in `artifacts/<vm>/windows/`: `autounattend.xml` and `vmctl-setup.ps1`.
   Windows Setup looks for the answer file at the root of every removable drive, so the seed
   is an ordinary SATA CD next to the install ISO and virtio-win.
3. **QEMU**: `virtio-blk-pci` disk with `bootindex=1`, install CD with `bootindex=2` (OVMF
   falls through to the CD only while the disk is empty), serial on stdio, no `-no-reboot`
   because Setup reboots several times.
4. **WinPE**: the answer file injects the `viostor` and `NetKVM` drivers from the virtio-win
   CD, wipes disk 0 (GPT: EFI 260 MB, MSR 16 MB, Windows), writes the TPM/Secure Boot/CPU/RAM
   bypasses into `HKLM\SYSTEM\Setup\LabConfig` (Windows 11 only), installs the edition.
5. **specialize** after the first reboot: computer name and time zone. Nothing runs here on
   purpose: a command failing in this pass blocks Setup with a dialog.
6. **OOBE** after the second reboot: local administrator user, autologon, no pages thanks to
   `HideEULAPage`, `HideLocalAccountScreen`, `HideOnlineAccountScreens`,
   `HideWirelessSetupInOOBE`, `ProtectYourPC=3` and the international settings declared in
   the oobeSystem pass as well.
7. **First logon**: `FirstLogonCommands` runs one short line (under 260 characters, Windows 10
   silently ignores longer `RunOnce` values):

        cmd.exe /c for %d in (D E F G) do if exist %d:\vmctl-setup.ps1 powershell.exe -NoProfile -ExecutionPolicy Bypass -File %d:\vmctl-setup.ps1

8. **vmctl-setup.ps1** logs every step to `C:\vmctl\setup.log` and to COM1, which is the
   serial the host sees. On the terminal they appear in order:

        [vmctl-windows] Setup script started on WIN11-LAB as <user>
        [vmctl-windows] Step: VirtIO guest tools
        [vmctl-windows] Step: Power settings
        [vmctl-windows] Step: OpenSSH Server            (Add-WindowsCapability, retried)
        [vmctl-windows] Step: SSH authorized key        (administrators_authorized_keys, ACL by SID)
        [vmctl-windows] Step: virtiofs share (WinFSP)   (only with shared_dir: WinFSP, VirtioFsSvc, desktop shortcut)
        [vmctl-windows] Step: setup command 1..N        (the profile's setup_commands, PowerShell)
        [vmctl-windows] Setup script finished
        ==> Windows installation complete!

    If a step fails, `==> Windows installation FAILED: <step>` arrives instead of the token and
    the guest still shuts down: the bootstrap ends at once with the error.

9. **Shutdown** (`shutdown /s`): the host waits up to 10 minutes for QEMU to exit by itself.
10. **Post-install**: the VM restarts headless, `vmctl` waits for SSH (`exit 0` as the probe,
    the login shell is `cmd.exe`) and runs the profile's `post_install_run`: `ver`, version and
    state of `sshd`, the content of `C:\vmctl\setup.log`, the drives present.

## 5. Check

```bash
vmctl shell windows11-unattended                    # cmd.exe over OpenSSH
ver
powershell -NoProfile -Command "(Get-Service sshd).Status; Get-PSDrive -PSProvider FileSystem"
dir Z:\                                             # the shared folder
```

## 6. Daily use and notes

- `vmctl stop` uses the ACPI button and waits up to 300 seconds (`acpi_poweroff_grace_sec`):
  the first shutdown after the bootstrap completes the OpenSSH "feature operations" and can
  take more than a minute. Do not force it.
- The profiles' `setup_commands` enable Remote Desktop in the guest; port 3389 is not
  forwarded on the host, add it if you need it.
- After changing `windows_config` just rerun the bootstrap: the prompt-free ISO is cached, the
  seed is rendered again.

## 7. Doing it by hand

The generated files are in `artifacts/<vm>/windows/` (`autounattend.xml`, `vmctl-setup.ps1`,
`seed.iso`). Any QEMU or libvirt that boots the prompt-free ISO with that seed and the
virtio-win ISO as extra CDs reproduces the same install; the serial (COM1) is optional, it
only serves to read the progress.

## 8. Windows 7 Ultimate (`windows7-unattended`)

Same command (`vmctl bootstrap-windows windows7-unattended`), same technique as kvm-lab's
`Windows7U`, with the differences Windows 7 imposes:

- **BIOS and MBR**: profile with `firmware.type: bios`, answer file with the "System Reserved"
  partition (100 MB, active) + Windows; no TPM/CPU bypass (`LabConfig`), not needed.
- **Drivers**: `viostor` injected in WinPE from the virtio-win CD (`driver_flavor: w7`); the NIC
  is `e1000e`, native on Windows 7, so NetKVM is not required. The Red Hat driver certificate is
  imported in the specialize pass (as SYSTEM) by `vmctl-cert.cmd` on the seed, which always
  exits 0 (a non-zero specialize command would block Setup with a dialog).
- **Guest agent**: the profile retains `guest_agent: true`. vmctl injects `vioserial` in
  WinPE and downloads the separate [Fedora agent 101.1.0 MSI](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/archive-qemu-ga/qemu-ga-win-101.1.0-1.el7ev/)
  on the host, pinned by `windows_config.guest_agent_msi` (`path`, `url`, `sha256`). The seed
  carries it as `vmctl-qga.msi`. Specialize Order 2 calls `vmctl-qga.cmd stage`, which copies
  the script and MSI to `C:\Windows\Setup\Scripts` and always exits 0.
  `SetupComplete.cmd` installs it as SYSTEM with `msiexec /i /qn /norestart` after Setup, when
  WMI is available. First logon checks the MSI result and running service before emitting the
  completion token; failures still shut down naturally. The agent is the only remote channel
  on 7 because SSH is unavailable. Logs: `C:\vmctl-qga.log`, `C:\vmctl-qga-msi.log`,
  `C:\vmctl-qga-exit.txt` and `C:\vmctl\setup.log`.
  Live diagnosis on Windows 7 RTM (7600, no SP1) established that agent 110.0.2 from
  virtio-win 0.1.285 fails in the loader with missing `api-ms-win-core-path-l1-1-0.dll`, before
  it can write a console log or open the channel. The child `VIOSerialPort` needs no separate
  INF: it is a raw device. `QueryDosDevice` found `\\.\Global\org.qemu.guest_agent.0`,
  `CreateFile` opened it successfully, and 101.1.0 answered ping, OS info and addresses with
  the same driver. Installing the MSI in specialize still fails during WMI/VSS registration;
  use SetupComplete. This pin is for Windows 7 only; Windows 10/11 keep their current flow.
- **Edition**: `image_index: 4` (Ultimate on the standard multi-edition media) and the generic
  KMS key `33PXH-7Y6KF-2VJC9-XBBR8-HVTHH`; it installs, it does not activate.
- **OOBE**: `SkipMachineOOBE`/`SkipUserOOBE` (they work here), `NetworkLocation=Work`,
  autologon.
- **First logon**: PowerShell 2.0, not elevated (UAC stays on): the script checks the guest
  agent service, runs `setup_commands`, writes `==> Windows installation complete!` on COM1 and shuts down. **No
  OpenSSH** on Windows 7, hence no SSH post-install: `check-vms` counts the install alone and
  `vmctl shell` is not available. Other virtio/SPICE guest tools remain a manual
  step with packages compatible with Windows 7; no virtiofs shared folder: virtio-win ships the
  `viofs` driver from Windows 8 on (`w8`, `w8.1`, `w10`, `w11` and the servers, no `w7`), so
  installing WinFSP would not be enough. To hand files to a Windows 7 guest, use QEMU's built-in
  SMB server (`-netdev user,smb=<dir>`, share `\\10.0.2.4\qemu`), which needs `smbd` on the host.
- **ISO**: `isos/windows7.iso` or the path in `local.json`; the prompt-free ISO is rebuilt once
  as for 10 and 11, with two differences the Windows 7 BIOS loader imposes: `boot/bootfix.bin` is
  deleted, not emptied (`etfsboot.com` hangs at "Booting from DVD/CD..." on an empty file), and
  xorriso keeps the exact ISO 9660 names (`-D -N -d`: `CDBOOT` looks up `BOOTMGR`, not
  `BOOTMGR.;1`). Both verified live; the cache stamp changes with them.

A fresh installation was verified with the pinned agent on Windows 7 RTM: SetupComplete's
MSI returned 0, `QEMU-GA` was running before the completion token, and ping, OS information
and addresses worked after starting the installed disk. To repeat the check (the first
command deletes the existing Windows 7 guest):

```sh
./bin/vmctl clean windows7-unattended
./bin/vmctl bootstrap-windows windows7-unattended --timeout 3600
./bin/vmctl start windows7-unattended --headless --background
# Wait for Windows to boot, then:
./bin/vmctl agent windows7-unattended ping
./bin/vmctl agent windows7-unattended
./bin/vmctl stop windows7-unattended
```

The clean is necessary: an installed disk boots ahead of the CD, so bootstrap on an existing
Windows disk does not rerun Setup. The unrelated USB xHCI driver warning (`VEN_1B36&DEV_000D`)
is separate from the agent. This test does not establish SHA-2 driver compatibility on other
Windows 7 media.
