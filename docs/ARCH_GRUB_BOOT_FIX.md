# Arch GRUB Boot Fix

`bootstrap-archinstall` uses a shared Arch bootstrap script. The `arch-noctalia-local`
first reboot once reached the UEFI GRUB loader and then dropped to the minimal
`grub>` prompt.

The fix is intentionally in the shared bootstrap path, not in the profile:

- install GRUB as a normal UEFI bootloader, without `--removable`;
- generate and validate `/boot/grub/grub.cfg`;
- build a standalone `EFI/GRUB/grubx64.efi` with an embedded first-stage config
  that searches the root filesystem by UUID and chain-loads `/boot/grub/grub.cfg`;
- copy the same standalone binary to `EFI/BOOT/BOOTX64.EFI` as NVRAM fallback;
- write ESP-side `grub.cfg` stubs for diagnostics and non-standalone recovery;
- flush `/dev/vda`, `/dev/vda1`, and `/dev/vda2` before printing the bootstrap
  completion token.

`run_and_expect` must not terminate QEMU immediately after seeing the completion
token. It now gives the guest time to finish `poweroff`, so final ESP writes are
not lost before the first installed boot.
