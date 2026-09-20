# Checkpoints: named copies of a stopped VM

`vmctl checkpoint` keeps named, self-contained copies of a VM's disk and firmware state,
to go back to later: `clean-install` right after a verified install, `before-update`
before an upgrade you may want to undo.

```bash
vmctl checkpoint create debian-server clean-install --note "fresh install, verified"
vmctl checkpoint list debian-server
vmctl checkpoint restore debian-server clean-install     # asks; --yes for scripts
vmctl checkpoint delete debian-server before-update --yes
vmctl --dry-run checkpoint restore debian-server clean-install   # the plan, nothing written
```

In the TUI the same actions live under **MAINTENANCE → Checkpoints** for a stopped VM:
one entry per checkpoint (creation time, bytes on the host, what its record knew about the
disk), *Create checkpoint*, and *Restore* / *Delete* behind a confirmation.

## What a checkpoint is

`artifacts/<vm>/checkpoints/<name>/`:

| File | Content |
|------|---------|
| `disk.qcow2` (`.img` for raw, `.vhd` for vpc) | a **full copy** of the disk written by `qemu-img convert` in the VM's own format (zero-detecting, sparse; `--compress` for a compressed qcow2) |
| `nvram.fd` | the EFI variable store (`OVMF_VARS.fd`) of an EFI profile, when it existed |
| `state.json` | the [record of what was known about the disk](PROFILES.md#what-is-known-about-the-disk-statejson-vmctl-status-the-tui) at that moment |
| `manifest.json` | name, VM, creation time, note, format, sizes, which of the files above are present |

The strategy is the full copy, for every format the profiles use (qcow2, raw for Windows
NT 4, vpc for the VHD import template). qcow2 internal snapshots would have excluded two
of them and would have lived inside the very file `vmctl clean` deletes; an overlay chain
would have tied the checkpoint to the current disk. A copy depends on nothing: it stays
valid however the current disk changes afterwards, it can be restored onto a VM whose disk
no longer exists, and it costs disk space, which `--compress` reduces for qcow2 at the
price of a slower write and restore. Checkpoints are listed in `vmctl status`'s
`ON HOST` figure of the VM only indirectly: `vmctl checkpoint list` shows their own size.

TPM state is not part of a checkpoint because there is none on plain QEMU (the Windows
profiles bypass the TPM check with `LabConfig`; swtpm exists only in the libvirt export).
A profile that declares `tpm` is refused rather than checkpointed halfway.

## When it refuses

Create and restore work on a powered-off VM. They refuse:

- a **running** VM (tracked background QEMU, or any QEMU on that disk): `vmctl stop <vm>` first;
- an **installation in progress** (a TUI job): let it finish or cancel it;
- a VM **defined in libvirt** (`export-libvirt`): `vmctl unexport-libvirt <vm>` first, libvirt owns the disk until then;
- an **invalid name**: letters, digits, `.`, `_`, `-`, up to 64 characters, starting with a letter or digit;
- an **existing checkpoint** with the same name on create, unless `--replace`;
- a **missing or incomplete** checkpoint on restore (the disk file is gone).

`restore` and `delete` ask for confirmation on a terminal; without one (a pipe, a script)
they stop unless `--yes` is given, so nothing is destroyed by a missing answer.

## How the files are protected

- **Create** assembles the copy in a hidden staging directory next to the checkpoints and
  renames it into place at the end; with `--replace` the old checkpoint is renamed aside
  first and removed last. A failed conversion leaves no checkpoint behind, and the source
  disk is only ever read.
- **Restore** converts the checkpoint into a hidden staging directory next to the disk
  while the current disk is untouched, then swaps disk, EFI vars and record with renames.
  If a rename fails, the files already moved are put back and the error says so. The
  previous files are deleted with the staging directory only after the swap succeeded.
  An EFI VM restored from a checkpoint that saved no vars gets its store reset (the
  firmware re-creates it from the template on the next boot).
- **Delete** renames the checkpoint aside and removes it afterwards, so a listing never
  shows a half-deleted checkpoint (hidden directories are skipped).

After a restore the disk's record is the one saved in the checkpoint (a `verified`
checkpoint restores a `verified` disk) and its origin says `restore <name>`; a checkpoint
taken without a record restores an `unverified` disk.

## Checkpoints and Clean VM

`vmctl clean <vm>` (TUI: *Clean VM*) deletes the disk, the EFI vars, the generated seeds
and logs, and **keeps the checkpoints**: they are the way back. It prints how many it kept
and how to restore one; `vmctl checkpoint restore <vm> <name>` on a cleaned VM recreates
the disk from the copy. `vmctl clean <vm> --checkpoints` removes them too, and
`vmctl checkpoint delete` removes one. `check-vms --restore` moves the whole artifact
directory aside and back, checkpoints included, so a validation run never touches them.
`tools/migrate_profile_names.py` moves them with the directory (the manifest's `vm` field
is informational).
