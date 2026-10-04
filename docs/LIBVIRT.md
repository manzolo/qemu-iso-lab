# Use an installed vmctl VM with libvirt

Install and provision with vmctl first, then stop the guest and export it:

```bash
vmctl stop debian-server
vmctl export-libvirt debian-server --dry-run
vmctl export-libvirt debian-server
virsh --connect qemu:///system start debian-server
virsh --connect qemu:///system domifaddr debian-server
```

The export writes `artifacts/<profile>/libvirt/<domain>.xml` and defines a
persistent domain. The existing disk is referenced by absolute path; it is
never copied. Open the same `qemu:///system` connection in virt-manager.
The host needs libvirt/virsh and permissions to define domains, plus access
for the libvirt QEMU account to the disk, firmware and any shared directory.
Windows TPM emulation also requires swtpm; shared directories require virtiofsd.

Options:

| Option | Meaning |
|---|---|
| `--name NAME` | Domain name; defaults to the profile ID, not its display name |
| `--connect URI` | Connection, default `qemu:///system`; paths must be accessible there |
| `--no-define` | Generate XML only, without invoking virsh |
| `--autostart` | Enable domain autostart after defining it |
| `--replace` | Replace an existing **stopped** domain; otherwise duplicates are an error |
| `--dry-run` | Print XML and planned commands, without writing files or changing libvirt |

Export refuses a missing disk or a guest detected running by PID, disk use or
QMP. Disk presence does not prove installation completed successfully; finish
the vmctl install/provision flow first. Existing running libvirt domains cannot
be replaced or unexported. Do not start the same disk with QEMU and libvirt
at the same time. After export, use virsh/virt-manager until unexporting:

```bash
virsh --connect qemu:///system shutdown debian-server
# Wait until `virsh domstate debian-server` reports "shut off".
vmctl unexport-libvirt debian-server
vmctl start debian-server
```

Repeat `--name` and `--connect` when unexporting a custom domain. Unexport calls
`virsh undefine --nvram`, preserves the disk, and saves/restores the existing
NVRAM bytes because libvirt otherwise deletes that file. Replacement uses the
same preservation procedure. No command uses `--remove-all-storage`.
The generated XML remains as a reference; it is not a VM snapshot or backup.

### Snapshots in virt-manager

An EFI profile is exported with its firmware as **qcow2 copies** under `artifacts/<vm>/libvirt/`
(`OVMF_CODE.qcow2`, `OVMF_VARS.qcow2`, converted from vmctl's raw files at every export): libvirt
takes internal snapshots of a pflash VM only with qcow2 variables and a loader in the same format.
With them virt-manager's snapshots work, with the VM running too (memory included, a revert in
seconds). Unexport converts the variables back into vmctl's raw file (whatever the guest changed in
libvirt is kept), removes the copies and undefines with `--snapshots-metadata`: the snapshots stay
inside the disk's qcow2 (`qemu-img snapshot -l`), vmctl boots the disk as it is, and
`qemu-img snapshot -d` removes one. A Windows profile (`hyperv: true`) gets the Hyper-V
enlightenments as an explicit list, not `mode='passthrough'`, which libvirt refuses to snapshot
running; `evmcs` only on Intel hosts.

After a snapshot revert libvirt may not give the disk back to its owner: unexport checks and, if the
disk or the variables belong to another user (libvirt-qemu), prints the `chown` to run as root
before `vmctl start` (which otherwise fails on "Permission denied").

## Profile translation

Memory/vCPUs, machine, EFI loader and existing NVRAM, disk bus/format, audio,
USB tablet and shared-directory settings come from the resolved profile,
including personal `local.json` overrides. BIOS has no pflash loader. QEMU's
portable `q35` and `pc` machine aliases let libvirt select the host's canonical
`pc-q35-<version>` and `pc-i440fx-<version>` names; explicit versioned names
are retained. Unversioned `pc-q35`/`pc-i440fx` are not valid QEMU machine names.
VHD uses the QEMU/libvirt driver name `vpc`.

The domain uses SPICE (local connection, no exposed TCP listener), QXL video
(or virtio for `virtio-gl` profiles), a guest-agent channel, and a TPM 2.0
emulator for Windows. The guest-agent channel does not install the guest agent.
Shared folders use virtiofs with memfd shared memory; guest mount/driver setup
from vmctl provisioning is retained. Custom QEMU video argument arrays and
host forwarding rules are not copied.

Networking uses libvirt's `default` network with DHCP. Use `virsh domifaddr`
for its address, then connect to the guest's SSH port 22, rather than the old
host-forwarded port. The default network must be active. If the installed guest
pins its network configuration to its old PCI interface name, adjust that guest
configuration for the libvirt adapter. Guest-agent address discovery additionally
requires an installed, running qemu-guest-agent in the guest.
