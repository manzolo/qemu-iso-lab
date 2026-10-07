# windows11-studio: recording on a Windows 11 desktop

The Windows counterpart of the Lubuntu studio (`tools/tour/vm/`, `docs/TOUR.md`): a Windows 11 VM in
libvirt where the clips about Windows are recorded (first one: installing the lab on a Windows that has
nothing yet, `setup-windows.ps1` from WSL to the dashboard).

## The VM

- libvirt domain **`windows11-studio`** (`qemu:///system`, network `default`, DHCP: the address is
  `virsh --connect qemu:///system domifaddr windows11-studio --source lease`). 12 GiB, 6 vCPU,
  `host-passthrough` CPU (nested virtualization reaches Windows: WSL2 gets `/dev/kvm`), q35, TPM
  emulator, QXL at 1600x900. **It lives in libvirt**: vmctl only installed it (`windows-11`, profile
  1.0.1, 2026-10-04); its disk was copied out once and nothing in the project touches it since.
- Files, all in `storage/hd/`: `windows11-studio.qcow2` (the disk), `windows11-studio_CODE.qcow2` and
  `windows11-studio_VARS.qcow2` (OVMF code and EFI variables **converted to qcow2**: internal snapshots
  need every writable image in qcow2, and libvirt wants the loader and the variables in the same format;
  the `<os>` has no `firmware='efi'` autoselection, the system ships no qcow2 build), and
  `windows11-studio.xml` (the domain, host paths: not in the repository).
- **Hyper-V enlightenments, as an explicit list** (`<hyperv mode='custom'>`: relaxed, vapic, spinlocks,
  vpindex, runtime, synic, stimer+direct, reset, frequencies, tlbflush, ipi, evmcs). Without them Windows
  resets on the spot (Kernel-Power 41, no dump) the moment WSL starts its VM. Not `mode='passthrough'`:
  it makes the VM unmigratable, so a snapshot with the VM running is refused. Not `reenlightenment`
  either: a live snapshot is taken, but restoring it fails (`cpu/msr_hyperv_reenlightenment ... -22`);
  it only matters for migrations between hosts with different TSC frequencies.
- **Snapshots are libvirt's**, internal, taken with the VM running (memory included: a revert resumes
  where it was, in about 20 s), managed from virt-manager (*View → Snapshots*) or `virsh snapshot-*`:

  | Snapshot | State |
  |---|---|
  | `1-windows-installato` | Windows as vmctl installed it + the studio (demo, agent, ffmpeg, Edge policies), no WSL |
  | `2-wsl` | + WSL 3.0.1 and Ubuntu 24.04 (Linux user `demo`, password `demo`), from `setup-windows.ps1`'s first two runs |
  | `3-qemu-iso-lab-pronto` | + qemu-iso-lab in WSL (third run: clone, `setup.sh`, kvm group) and `ubuntu-24.04-cloud` installed inside (3 min 5 s three levels deep), stopped |

  The agent inside a snapshot is the one of its day: after a revert, copy the current `agent.ps1` if it
  changed and restart the `StudioAgent` task.
- Delete it like every libvirt VM here: `virsh snapshot-delete` each snapshot (or `undefine
  --snapshots-metadata`), `virsh undefine windows11-studio --nvram`, then the files by hand. Never
  `--remove-all-storage` (it deletes the ISOs of the pools).

## Rebuilding it from scratch

1. **Install** with vmctl: `vmctl bootstrap-windows windows-11` (OpenSSH, the project key, the guest
   agent, the Hyper-V enlightenments of `hyperv: true`), then `vmctl stop windows-11`.
2. **Copy it out**, never move it:
   ```sh
   H=../storage/hd
   qemu-img convert -p -O qcow2 artifacts/windows-11/disk.qcow2 $H/windows11-studio.qcow2
   qemu-img convert -f raw -O qcow2 /usr/share/OVMF/OVMF_CODE_4M.fd $H/windows11-studio_CODE.qcow2
   qemu-img convert -f raw -O qcow2 artifacts/windows-11/OVMF_VARS.fd $H/windows11-studio_VARS.qcow2
   ```
3. **Define** it from `storage/hd/windows11-studio.xml` (or from `vmctl --dry-run export-libvirt
   windows-11`: name, paths, 12 GiB/6 vCPU, the qcow2 `<loader format='qcow2'>`/`<nvram format='qcow2'>`,
   the explicit `<hyperv mode='custom'>` list above and the `hypervclock` timer), `virsh start`.
4. **The studio**, over SSH as the profile user: `setup-studio.ps1`, `agent.ps1` into `C:\studio\`,
   `install-agent.ps1`, reboot, `wq '[Studio]::Resolution(1600,900)'`, close the Start menu the first
   logon leaves open; snapshot 1.
5. **WSL** from demo's desktop with `setup-windows.ps1` (through `wq`, as a user would): first run,
   reboot, second run, Enter on the pre-filled user, the password twice, `exit`; `wsl --shutdown`;
   snapshot 2. Third run: `y`, the sudo password, Enter on the welcome, the sudo password again; a first
   VM inside WSL to prove it; `wsl --shutdown`; snapshot 3.

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
| `<hyperv mode='passthrough'/>`: "'hv-passthrough' CPU flag prevents migration", no snapshot with the VM running | the explicit `mode='custom'` list |
| `reenlightenment` in that list: the live snapshot is taken, its revert fails (`msr_hyperv_reenlightenment`, -22) | leave it out |
| Internal snapshots refused while the EFI variables are a raw file; `firmware='efi'` finds no qcow2 build | OVMF code and variables converted to qcow2, explicit `<loader>`/`<nvram format='qcow2'>`, no autoselection |
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
| Edge asks "Save password?" over the Welcome form; "Restore pages" after the VM was reverted | `PasswordManagerEnabled 0` in `setup-studio.ps1`; set `exit_type` to `Normal` in the profile's `Preferences` before a take |
| The desktop shortcut's terminal takes a few seconds to appear, and Edge opens behind it, not maximized | wait for the window by title, then `[Studio]::Front` and `ShowWindow(h, 3)` |
| Localized words: `virsh domstate` says *terminato*, Windows answers in Italian | checks accept the localized words (or compare `LastBootUpTime`, a number) |
| A snapshot keeps the agent of its day | copy `agent.ps1` again after a revert if it changed |
| A fresh WSL Ubuntu has the `universe` lists missing until `apt update`: setup.sh skipped fzf, dialog, partclone... | fixed in vmctl: `host_setup.apt_lists_complete` (lists the native `Packages` files the suites' Release declares non-empty; while one is missing nothing is filtered) |
