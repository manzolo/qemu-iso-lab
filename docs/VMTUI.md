# vmtui

`vmtui` is a terminal dashboard over `vmctl`. It runs the same commands, echoes
each one before executing it or records it in the background installation log.
It remembers per-VM video preferences and tracks its background commands.

![Dashboard](screenshots/vmtui-dashboard.png)

- [Backends](#backends)
- [The dashboard](#the-dashboard)
- [The VM menu](#the-vm-menu)
- [Video profiles](#video-profiles)
- [After an installer](#after-an-installer)
- [Remote SPICE](#remote-spice)
- [Environment variables](#environment-variables)

## Backends

The TUI uses **fzf** when installed (fuzzy type-to-filter, cursor on the
suggested entry, version 0.40 or newer) and falls back to **dialog** otherwise.
Force one with `VMTUI_UI=fzf` or `VMTUI_UI=dialog`.

## The dashboard

The main screen lists every profile with live state:

| Glyph | Meaning |
|-------|---------|
| `●` | running |
| `■` | stopped, disk has data |
| `□` | disk prepared but still empty |
| `○` | no disk |

Each row also shows RAM/CPU, the firmware (`UEFI` or `Bios`), the SSH host port and,
for a disk with data, one flag for what is known about it followed by the bytes it
occupies on the host: `✓ 8.3G` boot verified, `~ 8.3G` installed but not verified yet,
`? 8.3G` no record for this disk, `! 8.3G` an unattended install that never completed
(the legend sits under the counters; the meaning of each state is in
[PROFILES.md](PROFILES.md#what-is-known-about-the-disk-statejson-vmctl-status-the-tui)).
The VM header spells it out: `8.3G on host / 32G capacity` and an `Install:` line with
the flow and the dates. None of these figures is the guest filesystem's used or free
space, and a pre-existing disk without a record keeps the `?` until a post-install or a
disk boot-check passes on it.
With fzf, column headings stay visible while scrolling, status markers are colored,
and column widths adapt to the terminal (narrow screens omit the description).
Installed VMs are listed first and the cursor starts on the VM you opened last.
The header counts profiles, VMs with data and running VMs.

Above the list: `Filter` (all / installed / running / by distro family) and
`Find` (substring on name or title). Below it: `Tools` (`vmctl status`, remote
hosts, clean all) and `Quit`.

`Ctrl-R` (or `F5`) rebuilds the dashboard in place: rows, the counters in the header and
the running markers, keeping the cursor where it was. The state moves while the menu is
open, an install finishes or a VM stops, and a letter could not be used for this because
letters filter the list. On the `dialog` backend the menu is rebuilt when you reopen it.

With fzf the shortcut bar follows the highlighted VM, without opening its menu.
The keys keep their meaning, and only relevant shortcuts are shown:

- Running: `Alt-A` screen, `Alt-S` SSH, `Alt-C` serial, `Alt-X` stop,
  `Alt-P` post-install. SSH, serial and post-install require SSH configuration.
- Stopped with disk data: `Alt-D` desktop, `Alt-H` headless, `Alt-S` SSH,
  `Alt-P` post-install and `Alt-U` reinstall. SSH and post-install require SSH configuration.
- Empty or missing disk: `Alt-U` the profile's install flow, `Alt-F` fetch ISO,
  `Alt-R` prepare disk and firmware.
- Installation in progress: `Alt-L` log and `Alt-X` cancel installation;
  `Alt-A` screen appears once the installer VM starts.

`Alt-U` displays the actual flow name, such as Alpine Bootstrap, Debian Preseed
Bootstrap or Windows Bootstrap; profiles without automation show Guided Provision.
For a stopped VM with disk data, the label starts with `reinstall:` followed by
the flow name. The same labels and shortcuts appear in the VM menu.

Unavailable shortcuts do nothing. The state is checked again before a dashboard
shortcut runs; `Ctrl-R` / `F5` refreshes the displayed snapshot. Alt-letter must reach the terminal:
some emulators keep it for their own menus, in which case the VM menu still has every
action a keystroke away.

Esc or Ctrl-C returns to the previous screen; from the dashboard it exits the TUI.

## The VM menu

With fzf, `Ctrl-R` or `F5` also refreshes the VM menu: the state summary and
available actions are rebuilt without returning to the dashboard. The highlighted
action stays selected if it is still available; otherwise the cursor moves to the
new suggested action. With `dialog`, reopen the menu to refresh it.

With fzf the VM menu uses the same contextual shortcuts, listed under the state
summary, plus `Alt-Enter` for the suggested action. A running VM without SSH
suggests Attach Display. Installation and restart actions remain accessible in
the full menu even when omitted from the shortcut bar. A shortcut picks its entry
only when available (Alt-X on a stopped VM does nothing but redraw).
Bare letters stay what they are in fzf, the filter;
the dialog backend has no shortcuts.

![VM menu](screenshots/vmtui-vm-menu.png)

Opening a VM shows the state, disk usage, ISO availability and SSH port above
a single contextual menu. With fzf, the summary uses the same muted labels,
bold values, status colors and highlighted shortcut keys as the dashboard;
narrow terminals split the summary into two rows. The dialog backend uses a
compact text summary. Only actions that make sense for the VM right now
are listed, and the suggested next step is marked `▶` and preselected, so Enter
does the obvious thing: install when there is no disk, boot when it is stopped,
SSH when it is running.

The layout follows the state of the disk. A VM whose disk has data opens on RUN;
its install flows move behind `Install / Reinstall...` (they wipe the disk, and the
entry says so). A VM without data keeps its install flow on top, where Enter runs it.
libvirt/remote and the physical-disk operations live in submenus at every state.

| Section | Entries |
|---------|---------|
| RUN (disk with data) | `Boot Desktop`, `Boot Headless`, `SSH Console` and `Serial Console` (with SSH provisioning), `First Boot` (cloud-init); while running: `Stop VM`, `Force Stop`, `Attach Display` (the screen of the headless VM in a VNC viewer), the consoles |
| INSTALL (no data yet) | The install flow matching the profile: `Full Bootstrap`, `Omarchy Bootstrap`, `Arch Bootstrap`, `Arch Install (Interactive)`, `Debian Preseed Bootstrap`, `Kickstart Bootstrap`, `AutoYaST Bootstrap`, `Alpine Bootstrap`, `Windows Bootstrap`, `Unattended Install`, `Cloud-Init Flow`, `Seeded Installer`, `Guided Provision`, `Installer Only`; then `Fetch ISO`, `Prepare VM` |
| MAINTENANCE | `Install / Reinstall...` (disk with data: the same flows, `Fetch ISO`, `Prepare VM`), `Clone VM...` (disk with data: a new local profile with its own disk copy, ports and MACs, asking what to do with the guest identity; [CLONE.md](CLONE.md)), `Checkpoints` (stopped VM with a disk: named full copies of disk + EFI vars, restore or delete; [CHECKPOINTS.md](CHECKPOINTS.md)), `Post-Install`, `Boot Check`, `Video Profile`, `Libvirt / Remote...` (`Export to libvirt`, `Remove from libvirt`, `Remote SPICE`), `Physical Disks...` (`Flash Empty Disk`, `Force Flash`, `Import Disk`), `Profile Details` |
| ADVANCED (stopped) | `Clean VM` (keeps checkpoints), `Delete ISO` |
| | `Back` (or Esc) returns to the dashboard; Esc in a submenu returns to the VM menu |

An installation in progress replaces all of this with `Installation Log`, `Attach
Display`, `Cancel Installation` and `Profile Details`.

The shortcuts do not care where an action sits: `Alt-U` on a disk with data runs the
reinstall flow that lives behind `Install / Reinstall...`, and a dashboard shortcut
accepts an action offered in a submenu, without opening it. A shortcut whose action is
not available at all (Alt-X on a stopped VM) still only redraws.

Destructive actions (`Cancel Installation`, `Stop VM`, `Clean VM`, `Delete ISO`, checkpoint
restore and delete, flash and import) ask for confirmation; flash and import also require
typing the device path. `Clean VM` keeps the VM's checkpoints.

`Force Flash` first asks for the copy mode: **full** writes every sector and
zeroes free space, **allocated** copies only the blocks the image allocates —
much faster on a sparse image of a large disk, but free space keeps its previous
bytes. `Flash Empty Disk` is always a full copy.

Both flash actions run `vmctl flash`: after a successful copy the backup GPT is
always repaired silently, then, on a terminal with at least 1 GiB free after a
final NTFS partition, a prompt offers to expand that partition and its filesystem.
The default is **No** (Enter, EOF or `n` keeps the image sizes). Checks,
dependencies, write order and failure handling are in
[Physical Disks: Flash and Import](IMPORT_DISKS.md#flash-a-vm-disk-to-a-physical-device).

`Import Disk` offers full-partition import, allocated-block import using Partclone
and GNU ddrescue, and resume of an allocated import. See [Physical Disk Imports](IMPORT_DISKS.md)
for supported layouts, dependencies and resume requirements.

## Video profiles

`Video Profile` picks the `video.variants` entry used by every start and install
action of that VM and remembers it under `~/.local/state/vmtui/`, so the TUI
never asks again until you change it. The current choice is shown in the menu
entry itself.

## After an installer

All `Bootstrap` actions, `Unattended Install` and `Omarchy Unattended Install`
run in the background. After the launch notice, the TUI returns to the VM menu;
closing the TUI or its terminal leaves the entire command running, including
post-install steps for bootstrap commands. `Installation Log` follows output
and errors; Ctrl-C returns to the menu without stopping the installation.
Logs are saved in `artifacts/<vm>/runtime/tui-job/output.log`.
The dashboard and VM menu show the command's status (running, completed,
failed, cancelled or interrupted); refresh or reopen the menu to update it.
While installation runs, the VM menu offers monitoring actions and hides
conflicting operations. `Attach Display` is visible throughout preparation and
opens the viewer once QEMU is running.

`Cancel Installation` asks for confirmation, then stops the background installation
and its child processes, followed by any VM still running for that profile. It works
during ISO preparation, installation and post-install, including jobs started before
the TUI was reopened. Processes that ignore termination are killed. The disk, ISO
and logs are preserved, but unsaved guest changes may be lost. The same action is
available from another terminal as `vmctl cancel-install <vm>`; it applies to jobs
launched by the TUI. Closing a viewer or leaving the TUI does not cancel the job.

To reinstall from scratch after cancellation, choose `Clean VM` (this deletes the
VM disk and generated artifacts), then the profile's install action. The cached
ISO is reused.

For install-only commands, use the boot actions after completion.

After an interactive installer that does not auto-boot the VM
(`Guided Provision`, `Cloud-Init Flow`, `Arch Install (Interactive)`,
`Installer Only`, `Seeded Installer`) the TUI offers
`Start headless + SSH post-install` / `Start with display` / `Done`, so the
install, boot and post-install chain finishes without navigating back through
menus. The full-flow `Bootstrap` actions already do this end to end and skip
the prompt.

## Remote SPICE

To run a VM on another machine and view it locally, create a remote host config:

```bash
cp vms/remotes.json.example vms/remotes.json
```

Edit it with the SSH target, the remote project path and the SPICE ports; the
TUI can also create and edit this file from `Remote Hosts` under `Tools`. Then
pick `Remote SPICE` in a VM menu: the TUI starts QEMU on the remote host with
`--spice-port`, opens an SSH tunnel and launches `remote-viewer` locally. If
`remote-viewer` is missing, it offers to install `virt-viewer` with the detected
package manager.

## Environment variables

| Variable | Effect |
|----------|--------|
| `VMTUI_UI` | `fzf` or `dialog`, skips auto-detection |
| `VMTUI_ROOT_DIR` | Repository root (default: resolved from the script location) |
| `VMTUI_CONFIG_DIR` | Directory holding `profiles/` and `remotes.json` (default: `<root>/vms`) |
| `VMTUI_STATE_DIR` | Where video preferences are stored (default: `~/.local/state/vmtui`) |


## Network lab

When the catalog has a `network_lab` router profile the dashboard shows a
"Network Lab" row above Tools: a submenu with the whole `vmctl lab` surface
(plan, status, install, install + export to libvirt, up, down, check, export,
unexport, libvirt round trip, clean). Install and clean ask for confirmation;
everything else runs the `vmctl lab ...` command directly and shows its output.
