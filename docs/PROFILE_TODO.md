# Profile status and backlog

## Selection rule

Add profiles that cover a new axis: firmware, installation flow, operating system family, graphical session, recovery tooling or installed-disk validation. Keep Alpine CI small.

## Implemented

- BIOS/EFI baselines: `debian-bios`, `debian-efi`, `fedora-server-efi` and the Alpine CI pair.
- Automated servers: `debian-server` (preseed), `almalinux-server` and `rocky-9` (kickstart), `ubuntu-server-ci` (autoinstall plus disk boot).
- Windows installation: `windows10-unattended`, `windows11-unattended`, `windows7-unattended`; import templates remain available separately.
- Immutable desktop: `fedora-silverblue` with ostree kickstart.
- openSUSE automation: `opensuse-tumbleweed-autoyast`.
- Ubuntu desktop flavors: five autoinstall recipes with explicit package, display-manager and graphical-session checks.
- Network lab: `pfsense-lab`, `pihole-lab`, `lubuntu-lab`.
- Non-Linux manual coverage: `freebsd`.
- Canonical names with legacy aliases, safe host-directory migration and pinned CI media with vendor checksums.

## Status semantics

Every tracked profile has `meta.status`:

- `manual`: interactive installation, live media or an import template; automation may still boot or inspect it.
- `unattended`: an automated installation/provisioning recipe exists; this is not a claim that this revision passed a live test.
- `experimental`: a known incomplete or unsettled flow. Currently Budgie (autologin still needs live verification), the package-only Ubuntu niri recipes and the custom Omarchy/NVIDIA flow.

`meta.verified` is the last live PASS date supplied by the maintainer. It is omitted when no date is recorded, and is never updated by unit tests or dry runs. A historical date does not certify subsequent profile changes. The list and HTML report show both fields separately from the current run's PASS/FAIL result.

Recorded dates:

- 2026-09-07: `lubuntu-24.04`, `kubuntu-24.04`, `xubuntu-24.04`, `ubuntu-mate-24.04`, `rocky-9`, `fedora-silverblue`, `opensuse-tumbleweed-autoyast`.
- 2026-09-06: the three network-lab profiles and all Windows profiles.
- Budgie has no recorded PASS date.

## Remaining work

- Validate Budgie autologin live with the new session assertions; promote it only after a real PASS.
- Revalidate the modified desktop assertions on installed guests, including greetd and OpenRC differences.
- Add a dedicated live/rescue profile for disk and boot repair.
- Consider a small Alpine EFI smoke profile alongside the BIOS baseline.
- Automate a FreeBSD installation to test provisioning assumptions outside Linux.
- Retrieve matching vendor checksums for discovery-selected Alpine/Fedora images; do not attach a static hash to a changing URL.
- Resume interrupted ISO downloads with HTTP Range requests: the Fedora archive cut a 2.4 GB
  transfer at 115 MB and the whole file had to be fetched again.
