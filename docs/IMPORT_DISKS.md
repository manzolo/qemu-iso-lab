# Physical Disk Imports

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

## Dependencies

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

## Supported Layouts

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

## Interruption and Resume

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
