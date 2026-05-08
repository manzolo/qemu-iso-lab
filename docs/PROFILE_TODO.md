# Profile TODO

This document tracks VM profile gaps that would improve coverage across
firmware types, install styles, operating system families, and CI flows.

## Selection Rule

New profiles should add at least one new coverage axis instead of being only
"another distro". Useful axes include:

- `bios` vs `efi`
- `pc` vs `q35`
- manual install vs unattended install
- ISO boot vs installed-disk boot
- desktop vs headless/server
- Linux vs non-Linux
- live/rescue vs normal installer
- graphical validation vs serial-console validation

## First Wave

These are the first five profiles to implement.

1. `alpine-installed-ci` - implemented
   Minimal installed-on-disk follow-up to `alpine-ci`.
   Coverage added: unattended-ish CI path, disk boot after install, stronger CI smoke test.

2. `debian-efi` - implemented
   Small Debian EFI baseline for manual install coverage.
   Coverage added: conservative EFI baseline for a mainstream Debian guest.

3. `debian-bios` - implemented
   Normal BIOS Debian guest outside the ultra-minimal Alpine CI case.
   Coverage added: BIOS regression coverage for a mainstream installer guest.

4. `fedora-server-efi` - implemented
   Fedora Server EFI baseline kept as a manual install profile.
   Coverage added: conservative Fedora Server EFI baseline without relying on fragile netinst automation.

5. `freebsd` - implemented
   Non-Linux guest for broader compatibility validation.
   Coverage added: different bootloader, device naming, serial behavior, and provisioning assumptions.

## Backlog

These are useful after the first wave.

- `ubuntu-server-headless`
  Headless, SSH-oriented server baseline for shell and post-install workflows.

- `windows11-installer`
  True Windows installer profile, separate from the current import templates.

- `almalinux` or `rockylinux`
  Stable enterprise-style Linux profile distinct from rolling/open desktop guests.

- `immutable-desktop`
  Example: Fedora Silverblue or openSUSE Aeon/Kalpa.
  Useful for testing provisioning assumptions on image-based desktop systems.

## Notes By Area

### CI

- Extend CI from "boot installer ISO until prompt" to "install minimal guest, then boot from disk".
- Add at least one reliable EFI-oriented CI guest once a serial-visible boot milestone is identified.

### Firmware

- Keep at least one mainstream `bios` guest in addition to `alpine-ci`.
- Maintain both `pc` and `q35` examples where the guest actually benefits from the distinction.

### Automation

- Avoid making Ubuntu autoinstall the only documented unattended path.
- Revisit non-Ubuntu unattended automation only with a source model proven to work reliably.

### Compatibility

- Keep one non-Linux guest in the catalog for broader QEMU/device assumptions.
- Keep one rescue/live profile for disk tooling and troubleshooting workflows.
