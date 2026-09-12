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
suggested entry, version 0.36 or newer) and falls back to **dialog** otherwise.
Force one with `VMTUI_UI=fzf` or `VMTUI_UI=dialog`.

## The dashboard

The main screen lists every profile with live state:

| Glyph | Meaning |
|-------|---------|
| `●` | running |
| `■` | stopped, disk has data (an OS is installed) |
| `□` | disk prepared but still empty |
| `○` | no disk |

Each row also shows RAM/CPU, the SSH host port and the disk size or ISO state.
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

With fzf the same Alt shortcuts as the VM menu work from the dashboard on the
highlighted VM, without opening its menu: `Alt-D` Boot Desktop, `Alt-U` its unattended
install, `Alt-S` SSH Console, `Alt-A` Attach Display, `Alt-C` Serial Console, `Alt-X`
Stop VM, `Alt-P` Post-Install. An action the VM does not offer right now (stopping a VM
that is not running) gets a one-line note instead. Alt-letter must reach the terminal:
some emulators keep it for their own menus, in which case the VM menu still has every
action a keystroke away.

Esc or Ctrl-C returns to the previous screen; from the dashboard it exits the TUI.

## The VM menu

With fzf, `Ctrl-R` or `F5` also refreshes the VM menu: the state summary and
available actions are rebuilt without returning to the dashboard. The highlighted
action stays selected if it is still available; otherwise the cursor moves to the
new suggested action. With `dialog`, reopen the menu to refresh it.

With fzf the everyday actions also have Alt shortcuts, listed under the state
summary: `Alt-D` Boot Desktop, `Alt-U` this profile's unattended install (Full
Bootstrap, Debian Preseed Bootstrap, Windows Bootstrap, ReactOS Bootstrap...),
`Alt-S` SSH Console, `Alt-A` Attach Display, `Alt-C` Serial Console, `Alt-X`
Stop VM, `Alt-P` Post-Install and `Alt-Enter` the suggested action. A shortcut
picks its entry only when the menu offers it right now (Alt-X on a stopped VM
does nothing but redraw). Bare letters stay what they are in fzf, the filter;
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

| Section | Entries |
|---------|---------|
| INSTALL | The install flow matching the profile: `Full Bootstrap`, `Omarchy Bootstrap`, `Arch Bootstrap`, `Arch Install (Interactive)`, `Debian Preseed Bootstrap`, `Kickstart Bootstrap`, `Unattended Install`, `Cloud-Init Flow`, `Seeded Installer`, `Guided Provision`, `Installer Only` |
| RUN | `Boot Desktop`, `Boot Headless`, `Stop VM` and `Attach Display` (only while running: the screen of the headless VM in a VNC viewer), `SSH Console` (only with SSH provisioning, also while an installer is running on an empty disk), `First Boot` (cloud-init), `Remote SPICE` |
| MAINTENANCE | `Video Profile`, `Post-Install`, `Boot Check`, `Fetch ISO`, `Prepare VM`, `Profile Details` |
| ADVANCED | `Clean VM`, `Delete ISO`, `Flash Empty Disk`, `Force Flash`, `Import Disk` |
| | `Back` (or Esc) returns to the dashboard |

Destructive actions (`Cancel Installation`, `Stop VM`, `Clean VM`, `Delete ISO`, flash and import) ask
for confirmation; flash and import also require typing the device path.

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
