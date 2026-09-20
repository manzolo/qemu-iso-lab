# Cloning a VM

`vmctl clone <origin> <destination>` makes an independent copy of a stopped VM under a
new profile name. The copy has its own disk, EFI vars, SSH key, host ports and MAC
addresses; what the guest wrote about itself (hostname, machine-id, SSH host keys) is a
separate decision, made explicit by `--identity`.

```bash
vmctl clone debian-server debian-server-2
vmctl clone debian-server debian-server-2 --identity regenerate   # new hostname, machine-id, SSH host keys
vmctl clone ubuntu-gnome-24.04 ubuntu-test --ssh-port 2400
vmctl --dry-run clone debian-server debian-server-2               # the plan and the new profile, nothing written
vmctl start debian-server-2 --headless --background && vmctl shell debian-server-2
```

In the TUI: **MAINTENANCE → Clone VM...** on a stopped VM whose disk has data: the new
name, then the identity choice (`regenerate` is offered only when the guest supports it),
then the same command.

## What the clone is

| Piece | In the clone |
|-------|--------------|
| profile | a **complete** entry in `vms/profiles/local.json`: the origin's resolved profile with every path under `artifacts/<destination>/`, `name` suffixed with `(clone of <origin>)`, `meta.clone_of`, `meta.cloned_at`, no `meta.verified` (that date is about the recipe, not this copy) |
| SSH host port | its own: `--ssh-port`, or the first free port from 2300 not forwarded by any profile |
| `hostfwd` forwards | one new free host port each (guest ports unchanged) |
| MAC addresses | explicit `mac` values are dropped; the default MAC derives from the disk path, so the clone's NICs differ on their own |
| disk | a full `qemu-img convert` copy in the same format: no backing file, no dependency on the origin image |
| EFI vars | copied (`OVMF_VARS.fd`), so the boot entries the firmware wrote still point at the installed system |
| SSH key pair | `artifacts/<origin>/ssh/` copied: the guest's `authorized_keys` holds that public key, without it the clone could not be reached |
| `state.json` | copied, with the origin noted (`vmctl status` shows the clone as `verified` if the origin was, origin `clone`) |
| not copied | PID files, sockets, logs, TUI jobs, checkpoints, generated installer seeds: they describe the origin |

The profile is written **complete**, not as an override of the origin: later edits of the
tracked origin do not reach the clone, which is what a clone is for. The tracked profile
files are never written; an existing `local.json` is merged into, with a `.bak` copy
beside it.

To get rid of a clone: `vmctl clean <destination> --remove-profile` removes its disk,
artifacts and checkpoints **and** deletes its entry from `local.json` (the previous file
is kept as `local.json.bak`; other entries, including overrides of the origin, stay). In
the TUI, *Clean VM* on a clone asks whether to delete the clone or only its artifacts.
`--remove-profile` is refused for a tracked profile: its `local.json` entry would be an
override, not the profile. A plain `vmctl clean <destination>` keeps the profile entry,
which is then just a catalog entry like any tracked profile before its first install.

## Guest identity

Everything inside the disk is byte-identical to the origin: the hostname, `/etc/machine-id`
(what systemd, journald, DHCP clients and many licences key on), the SSH host keys (two
machines presenting the same host key is what SSH warns about), static addresses, and
whatever the installed system decided about itself. Changing the profile changes none
of it.

- `--identity keep` (default): the command states what is kept and how to change it by hand.
- `--identity regenerate`: the clone is booted once headless and, over SSH with the
  copied key and passwordless sudo, gets `<destination>` as hostname (`hostnamectl`, or
  `/etc/hostname` + `hostname` on OpenRC), the old name rewritten in `/etc/hosts`, a new
  `/etc/machine-id` (`systemd-machine-id-setup` or `dbus-uuidgen`), new SSH host keys
  (`ssh-keygen -A`); then it is stopped. This is offered for the Linux guests this lab
  provisions over SSH. It is refused, with the manual recipe, for Windows (sysprep),
  pfSense (config.xml), ReactOS, FreeBSD (`sysrc`, `service sshd keygen`) and NixOS
  (`networking.hostName` lives in `configuration.nix`), and for profiles without SSH
  provisioning. Static addresses are never touched: they are the user's to change.

If the regeneration fails (no SSH within `--timeout`), the clone is removed again, so no
half-identified copy stays published; `--identity keep` gives the plain copy.

## When it refuses

- the origin is **running**, **installing** or **defined in libvirt** (`vmctl stop`,
  wait or cancel, `vmctl unexport-libvirt` first);
- the origin is a **network lab member** (`network_lab`): its static address and the
  router's NAT rules are part of the topology, a copy would collide with it;
- the origin has **no disk image**, or declares `tpm` (no TPM state exists to copy);
- the destination **name** is invalid (lowercase letters, digits, `.`, `-`), already a
  profile or a deprecated alias, or `artifacts/<destination>/` is not empty;
- `--ssh-port` names a port another profile forwards.

## What a failure leaves behind

The copy is assembled in `artifacts/.clone-<destination>-<pid>/` and renamed into place
only when complete; `local.json` is written after that. A failed disk copy therefore
leaves the origin untouched and no trace of the clone. The origin is only ever read.
