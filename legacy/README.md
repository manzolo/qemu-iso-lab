# legacy/

Historical prototypes superseded by `bin/vmctl`. Kept for reference, no
longer used by the current workflows.

- `setup-vhd.sh` — created a VHD disk, copied OVMF_VARS, and generated
  `run-*.sh` scripts for CachyOS. Today: `vmctl prep` and `vmctl install`.
- `run-install.sh` — hardcoded CachyOS installer (EFI + virtio + GTK).
  Today: `vmctl install <vm>`.
- `run-boot.sh` — boots the already-installed disk. Today: `vmctl start <vm>`.

These scripts were built around a single `cachyos-30g.vhd` disk in the
project root, with hardcoded paths and names. The current model (JSON
profiles under `vms/profiles/`, isolated per-VM artifacts under
`artifacts/<vm>/`) covers the same scenario declaratively and for multiple
distros.
