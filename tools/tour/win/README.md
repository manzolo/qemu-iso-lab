# windows11-studio: recording on a Windows 11 desktop

The Windows counterpart of the Lubuntu studio (`tools/tour/vm/`, `docs/TOUR.md`): a Windows 11 VM in
libvirt where the clips about Windows are recorded (first one: installing the lab on a Windows that has
nothing yet, `setup-windows.ps1` from WSL to the dashboard).

## The VM

- libvirt domain **`windows11-studio`** (`qemu:///system`, network `default`, DHCP: the address is
  `virsh --connect qemu:///system domifaddr windows11-studio --source lease`). 12 GiB, 6 vCPU,
  `host-passthrough` CPU (nested virtualization reaches Windows: WSL2 gets `/dev/kvm`), q35/OVMF, TPM
  emulator, QXL at 1600x900.
- Independent of the lab: its disk is a **full copy** of `artifacts/windows-11/disk.qcow2` (the unattended
  `windows-11` profile) in `storage/hd/windows11-studio.qcow2`, EFI vars in
  `storage/hd/windows11-studio_VARS.fd`. `vmctl clean windows-11` does not touch it, and it does not touch
  the lab. The XML is vmctl's own `export-libvirt` rendering with the name, paths, memory and CPUs changed,
  plus **`<hyperv mode='passthrough'/>`** in `<features>` and a `hypervclock` timer. Without the Hyper-V
  enlightenments Windows resets on the spot (Kernel-Power 41, no dump, nothing in the host log) the moment
  WSL starts its VM: Hyper-V runs fine as the L1 hypervisor until it launches an L2 guest. Reproduced twice
  on 2026-10-04, gone with the enlightenments.
- **Clean snapshot**: `storage/hd/windows11-studio.clean.qcow2` + `windows11-studio_VARS.clean.fd` =
  Windows with the studio tools and **no WSL**. To record the install again from zero:
  `virsh shutdown`, copy both `.clean` files over the live ones, `virsh start`.
- **WSL snapshot**: `windows11-studio.wsl.qcow2` + `windows11-studio_VARS.wsl.fd` = the same after
  `wsl --install` and its reboot, with Ubuntu 24.04 initialised (Linux user `demo`): the point to
  restart from for the part after WSL (clone, `./setup.sh`, the dashboard) without reinstalling it.
- **Ready snapshot**: `windows11-studio.ready.qcow2` + `windows11-studio_VARS.ready.fd` = qemu-iso-lab
  installed in WSL (`~/qemu-iso-lab`, `setup.sh` done, `demo` in the kvm group) and `ubuntu-24.04-cloud`
  installed inside it (6 min 50 s three levels deep, 49 s on the host): for clips that start from a working
  lab on Windows. The VM serves other recordings too: the snapshots are its known states, not its purpose.
- The domain's XML is saved beside the disks as `storage/hd/windows11-studio.xml` (host paths, so not in
  the repository): `virsh define` it to rebuild the VM on this host.
- Delete it like every libvirt VM here: `virsh undefine windows11-studio --nvram` and remove the files by
  hand. Never `--remove-all-storage` (it deletes the ISOs of the pools).

## Rebuilding it from scratch

1. **The source disk**: a `windows-11` installed by vmctl, `vmctl bootstrap-windows windows-11` (OpenSSH,
   the project key and the guest agent come with it). With that VM stopped, copy it, never move it:
   ```sh
   qemu-img convert -p -O qcow2 artifacts/windows-11/disk.qcow2 ../storage/hd/windows11-studio.qcow2
   cp artifacts/windows-11/OVMF_VARS.fd ../storage/hd/windows11-studio_VARS.fd
   ```
2. **The domain**: start from `vmctl --dry-run export-libvirt windows-11`, change the name, the disk and
   VARS paths, memory (12 GiB) and vCPUs (6), add `<hyperv mode='passthrough'/>` in `<features>` and
   `<clock offset='localtime'><timer name='hypervclock' present='yes'/></clock>`; `virsh define`,
   `virsh start`, the address from `domifaddr --source lease`. (Once the `hyperv` profile field is in vmctl,
   the export renders both blocks itself.) Or `virsh define storage/hd/windows11-studio.xml`.
3. **Inside Windows**, over SSH as the profile user: `setup-studio.ps1` (as administrator), then
   `agent.ps1` into `C:\studio\` and `install-agent.ps1`; reboot (demo logs on, the agent starts), then
   `wq '[Studio]::Resolution(1600,900)'`.
4. **Snapshots**: with the VM shut down, `cp --sparse=always` the disk and the VARS to `.clean`, `.wsl`,
   `.ready`; restore by copying them back over the live files. **After a restore**, copy the current
   `agent.ps1` again and restart the `StudioAgent` task: a snapshot keeps the agent of its day.

## Inside Windows (`setup-studio.ps1` + `install-agent.ps1`, already applied)

- User **`demo`** (administrator, automatic logon, throwaway password `demo`): what the screen shows. The
  profile's own user is still there and is what SSH uses.
- Studio-only settings: administrators elevate **without the consent prompt** (`ConsentPromptBehaviorAdmin
  0`: automation cannot answer the secure desktop; the narration says Windows asks for admin rights), no
  lock screen, no sleep, and Edge without its first-run pages (policies `HideFirstRunExperience` and
  `TranslateEnabled 0`: four pages of sign-in, Google import and consent stood between `-Web` and the
  dashboard).
- `C:\tools\ffmpeg` (BtbN static build): `gdigrab` captures the desktop.
- **The agent**: `C:\studio\agent.ps1`, scheduled task `StudioAgent` at demo's logon (comes back after a
  reboot), hidden PowerShell in demo's session. It runs every `C:\studio\q\*.ps1` in name order, writes
  the output to `C:\studio\done\<name>.log` and deletes the script. SSH alone cannot type into the desktop
  (session 0); the agent can.

## From the host

```sh
tools/tour/win/w 'ver'                                # SSH as the profile user (cmd.exe), key of artifacts/windows-11/ssh
tools/tour/win/w 'powershell -NoProfile -Command "Get-Date"'
tools/tour/win/wq '[Studio]::Resolution(1600,900)'   # PowerShell inside demo's desktop, through the agent; prints its output
```

Helpers in scope for `wq` scripts (defined in `agent.ps1`):

| Helper | What it does |
|---|---|
| `Focus <pid or title>` | the window to the foreground (synthetic Alt + `SetForegroundWindow`; `AppActivate` alone fails from a background process). Esc first only when the window is not already in front (a Start menu left open after logon keeps the keyboard); never into a terminal that has it |
| `TypeIn "text" [ms]` | SendKeys, one call per word (a call costs ~0.3 s), special characters escaped; works with the Italian layout |
| `Key "{ENTER}"` | a raw SendKeys spec (`%{F4}`, `^c`, `{TAB}`) |
| `Glide x y [sec]` | moves the real pointer smoothly |
| `RecStart C:\studio\x.mkv` / `RecStop` / `RecTime` | ffmpeg gdigrab 25 fps H.264, stopped with `q` (a clean file) |
| `[Studio]::Resolution(w,h)` | display mode, persistent |

Typical: `$p = Start-Process powershell -PassThru; Focus $p.Id; TypeIn "wsl --status"; Key "{ENTER}"`.
Fetch a recording with `scp` (same key and user as `w`). Quote with care: `wq` sends the text as a .ps1,
so `$` is PowerShell's, not the host shell's (single quotes on the host side).

## Pitfalls met (2026-10-04)

| Pitfall | What to do |
|---|---|
| Windows resets on the spot (Kernel-Power 41, no dump, nothing on the host) when WSL starts its VM | the Hyper-V enlightenments in the XML (see *The VM*) |
| SSH lands in session 0: nothing typed or recorded reaches the desktop | the agent in demo's session |
| `Type` is a PowerShell alias of `Get-Content`, and aliases beat functions | the helper is `TypeIn` |
| `AppActivate` from a background process does not take the focus | `Focus` (Alt + `SetForegroundWindow`) |
| An Esc sent before focusing reaches a Linux terminal as `^[`: `^[y` refused, a user named `demodemo` | `Focus` sends Esc only when the window is not already in front |
| SendKeys costs ~0.3 s a call | one call per word |
| WSL's first start pre-fills the Unix user with the Windows name | Enter only |
| A Ctrl+C at WSL's password prompt hung the WSL service (even `wsl --shutdown`) | only a Windows reboot freed it; do not interrupt that prompt |
| `\n` does not survive `wsl.exe -- sh -c '...'` from PowerShell (arrives as `n`) | one `echo` per line, or a script in `C:\studio` run as `/mnt/c/studio/x.sh` |
| A window title set through `-ArgumentList` arrives mangled | address windows by process id |
| A check looking for `setup-windows.ps1` in command lines finds itself (like `pkill -f`) | exclude the check's own process |
| The blinking cursor keeps "the screen stopped changing" from ever being true | wait on what the screen says, not on its hash |
| The reboot `wsl --install` asks for ends the recording | record in segments, join them with ffmpeg's concat demuxer |
| Edge's first start shows four pages | the Edge policies of `setup-studio.ps1` |
| Localized words: `virsh domstate` says *terminato*, Windows answers in Italian | checks accept the localized words (or compare `LastBootUpTime`, a number) |
| A snapshot keeps the agent of its day | copy `agent.ps1` again after every restore |
| A fresh WSL Ubuntu has the `universe` lists missing until `apt update`: setup.sh skipped fzf, dialog, partclone... | fixed in vmctl: `host_setup.apt_lists_complete` (lists the native `Packages` files the suites' Release declares non-empty; while one is missing nothing is filtered) |
