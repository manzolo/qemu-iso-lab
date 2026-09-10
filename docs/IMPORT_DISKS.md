# Physical Disks: Flash and Import

Two destructive, sudo-only commands move a VM between an image and a real disk:
`vmctl flash` writes a VM image to a whole block device, `vmctl import-device`
reads a whole block device into a VM image. Both take the device twice
(`--device` and `--confirm-device`), refuse the host root disk and mounted
targets, and support `--dry-run`. In `vmtui` they are **Flash Empty Disk**,
**Force Flash** and **Import Disk** in the ADVANCED group; both TUI flash entries
run the same `vmctl flash` and therefore show the same end-of-copy prompt.

## Flash a VM disk to a physical device

Select the correct whole-disk target: flashing overwrites it. Replace `my-vm`
with a configured VM and `/dev/sdX` with the destination.

```bash
vmctl flash my-vm --device /dev/sdX --confirm-device /dev/sdX
```

An empty target is required by default. For a target with existing partitions
or signatures, add `--force-target`; this authorizes overwriting the target and
does not imply consent to expansion.

### GPT repair (always)

After the copy, the backup GPT is relocated to the last sector of the target
(`sgdisk -e`), silently and every time the written disk is GPT, including images
that already carried a misplaced backup table. Without it a 64 GiB image on a
477 GiB disk declares that the disk ends at 64 GiB and the rest stays invisible
to the guest. This is a correctness fix of the table, not a choice: it also runs
when expansion is declined. The written bytes are probed with `wipefs`, not the
lsblk/udev cache, so a slow udev cannot skip it.

### Expansion (asked, default No)

Partition expansion is a separate choice. On a terminal, if at least 1 GiB
remains after a final GPT Microsoft basic-data partition with NTFS, the command
asks at the end:

```text
The disk has 413.0 GiB unallocated after the last partition (sdd3, NTFS 63.7 GiB).
Expand the partition and filesystem to use the entire disk? [y/N]
```

Enter, `n` or EOF leaves the partition and filesystem sizes as in the image.
Answer `y` to expand. Without a TTY on stdin, it does not ask or expand: one line
reports the free space and how to expand the existing copy later without
reflashing.

For scripts, make the choice explicit with one of these mutually exclusive flags:

```bash
# Preserve image partition and filesystem sizes; repair GPT only.
vmctl flash my-vm --device /dev/sdX --confirm-device /dev/sdX --no-expand

# Expand the supported final NTFS partition and filesystem without asking.
vmctl flash my-vm --device /dev/sdX --confirm-device /dev/sdX --expand
```

`--expand` can also reclaim less than 1 GiB. Recovery partitions, BitLocker,
Linux and other non-NTFS filesystems, MBR layouts and volumes rejected by
`ntfsresize` are never offered for expansion, even with `--expand`; one line
states the reason (for `ntfsresize`, its own message). No partitions are moved.

### Dependencies

GPT handling needs `sgdisk` (gdisk), `sfdisk` (fdisk/util-linux), `blkid` and
`blockdev`; they are checked before copying a GPT or container image so a missing
tool cannot strand the copy. Expansion also needs `ntfsresize` from `ntfs-3g`;
when it is missing, free space is left unallocated with a warning.

```bash
# Debian / Ubuntu
sudo apt install gdisk fdisk ntfs-3g
# Arch
sudo pacman -S gptfdisk util-linux ntfs-3g
```

### What expansion does, in order

The write order is **GPT repair (`sgdisk -e`) → partition enlargement
(`sfdisk -N`) → filesystem enlargement (`ntfsresize`)**; the reverse order fails
with "device is too small".

1. Read-only `ntfsresize --check` and `ntfsresize --no-action` run on the
   original filesystem before anything is offered. A dirty or hibernated volume
   is never forced: the partition stays unchanged and the reason is reported.
2. The partition table is saved as `flash-partitions.sfdisk` beside the VM
   image (replaced by the next expansion). Mounts are checked again: if the
   desktop automounted the USB partitions, the error lists the mountpoints and
   how to unmount them; nothing is unmounted forcibly.
3. Every kernel reread in these steps first waits for udev (`udevadm settle`)
   and retries a busy device up to five times: right after a table write, udev
   and udisks probe the new partitions and hold them open for a moment, so a
   `blockdev --rereadpt` one second after `sgdisk -e` fails with EBUSY (seen
   live). A partition that is really mounted stops with its mountpoints instead.
4. The last partition is enlarged with `sfdisk -N` and wiping disabled. Start,
   type, UUID and the other partitions are preserved; the geometry is verified
   on nodes, starts, sizes, types and UUIDs, and the kernel must see the new
   size.
5. A second `ntfsresize --no-action` on the enlarged partition must pass. Only
   then does the real `ntfsresize` run.

If anything fails before the filesystem write, the original table is restored
from the backup while the target is unmounted and its geometry still matches;
restore failures report both errors and the backup location. Once the real NTFS
resize has started, a failure keeps the enlarged partition: the filesystem may
already have grown, so shrinking the partition back could truncate it. Inspect
or repair the filesystem before retrying its resize. Every post-copy error
states that the image itself was copied. Windows may run a consistency check on
first boot after a successful resize.

### Expanding a copy later

Keep the target unmounted, save its partition table and use a partition editor
to extend the final NTFS partition without changing its starting sector. Once
the kernel sees the larger partition, run `sudo ntfsresize --no-action /dev/sdXN`
on that partition, and only if it succeeds `sudo ntfsresize /dev/sdXN`
(`/dev/sdXN` is the NTFS partition, not the whole disk). The GPT repair already
performed by flash remains in place; no new flash is needed.

## Import a physical disk into a VM image

`vmctl import-device` reads an unmounted physical disk into the disk image of
an existing VM profile. The source is read-only; the target VM image is replaced.
Stop the target VM first.

The default import copies full partitions, trimming trailing unpartitioned space
where possible. Add `--allocated-only` to skip free blocks inside supported
filesystems instead:

```bash
bin/vmctl import-device windows11-template --device /dev/sdX --confirm-device /dev/sdX --allocated-only
```

Use the actual source device in both device arguments. In `vmtui`, choose
**Import Disk**, then **Allocated blocks**. Full import remains available in the
same menu.

### Dependencies

`vmctl setup` checks the tools and includes them in its package installation offer:

```bash
# Debian / Ubuntu
sudo apt install gddrescue partclone fdisk
# Arch
sudo pacman -S ddrescue partclone util-linux
```

GNU ddrescue supplies both `ddrescue` and `ddrescuelog`. Partclone supplies
`partclone.extfs`, `partclone.ntfs`, `partclone.fat` and `partclone.exfat`; the matching executable
is required when that filesystem is present. QEMU tools remain required.

### Supported Layouts

- GPT and primary MBR partitions, with 512-byte logical sectors.
- Allocated-block mapping for ext2/3/4, NTFS, FAT and exFAT.
- Other filesystems, unopened encrypted partitions and inactive LVM/RAID
  containers are copied in full. Active device mappings must be deactivated.
- Extended MBR partitions, nested layouts and 4Kn disks are rejected.
- Destination formats: RAW and QCOW2, without subformat options.

The domain map includes filesystem metadata and allocated blocks. It also copies
all areas outside partitions, including bootloader gaps and the backup GPT.
Large unpartitioned gaps therefore still take time to read. The logical disk
size and partition boundaries are preserved; this mode does not shrink filesystems.

The source must stay unmounted and unchanged throughout the operation, including
between attempts. Filesystem check failures stop the import; checks are not forced
or bypassed. In particular, cleanly shut down Windows rather than importing a
hibernated NTFS volume.

### Interruption and Resume

State is kept next to the destination, for example
`artifacts/windows11-template/disk.qcow2.allocated-import/`:

- `source.raw`: sparse temporary disk, initially zero outside copied blocks.
- `domain.map`: blocks that must be read.
- `rescue.map`: ddrescue's progress and read-error states.
- `manifest.json`: source geometry, target identity and allocation-map fingerprint.

Resume with the same arguments and `--resume`, or choose **Resume allocated import**
in the TUI:

```bash
bin/vmctl import-device windows11-template --device /dev/sdX --confirm-device /dev/sdX --allocated-only --resume
```

Resume regenerates the allocation map and verifies source/target identity. It also
compares previously recovered bytes with the source, so verification reads that
portion again. A changed source, changed target, missing RAW or inconsistent map
stops the operation. An existing state directory is never silently discarded;
move it aside to start over if the source changed. Do not modify its contents.

The import is not considered complete until `ddrescuelog` confirms every required
block was recovered. Unread or bad required sectors leave the existing VM image
untouched and retain the temporary state. This mode does not perform aggressive
retries or replace a dedicated damaged-disk recovery workflow.

Conversion runs into a separate staged file; QCOW2 is checked before the final
replacement. The old VM image remains in place until that step succeeds. Allow
space for the RAW, converted image and any existing VM disk simultaneously.
Successful imports remove their temporary state.

The domain-map interface is documented by
[Partclone](https://partclone.org/usage/partclone.php); copy, resume and completion
checks follow the [GNU ddrescue manual](https://www.gnu.org/software/ddrescue/manual/ddrescue_manual.html).
