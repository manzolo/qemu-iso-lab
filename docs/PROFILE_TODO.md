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
- Non-Linux manual coverage: `freebsd`, `reactos`.
- Ubuntu desktop history: nine manual profiles, one per LTS from 8.04 to 24.04, on the official desktop ISOs (`ubuntu-lts.json`). `ubuntu-8.04-unattended` to `ubuntu-18.04-unattended` install them with `bootstrap-preseed` on the d-i alternate/server media (live PASS 2026-09-12, see below; 16.04 and 18.04 use the shared `verify-desktop`); 20.04+ would be autoinstall + `ubuntu-desktop`.
- Canonical names with legacy aliases, safe host-directory migration and pinned CI media with vendor checksums.

## Status semantics

Every tracked profile has `meta.status`:

- `manual`: interactive installation, live media or an import template; automation may still boot or inspect it.
- `unattended`: an automated installation/provisioning recipe exists; this is not a claim that this revision passed a live test.
- `experimental`: a known incomplete or unsettled flow. Currently the package-only Ubuntu niri recipes and the custom Omarchy/NVIDIA flow.

`meta.verified` is the last live PASS date supplied by the maintainer. It is omitted when no date is recorded, and is never updated by unit tests or dry runs. A historical date does not certify subsequent profile changes. The list and HTML report show both fields separately from the current run's PASS/FAIL result.

Recorded dates:

- 2026-09-12: `ubuntu-8.04-unattended`, `ubuntu-10.04-unattended`, `ubuntu-12.04-unattended`, `ubuntu-14.04-unattended`, `ubuntu-16.04-unattended`, `ubuntu-18.04-unattended`, each reinstalled from a clean disk with the final recipe (autologin session, passwordless sudo, legacy SSH verified in-guest).
- 2026-09-09: the whole unattended matrix, 28 profiles reinstalled from scratch
  (`check-vms --clean-first --parallel 2 --timeout 3600`, reports under
  `artifacts/check-vms/20260909-*`): every Arch/CachyOS, Debian, AlmaLinux/Rocky, Fedora
  (niri-dms and Silverblue), Alpine niri, Tumbleweed AutoYaST, Ubuntu flavor (Budgie and
  GNOME included), network-lab and Windows 7/10/11 profile passed. `arch-omarchy-nvidia`
  failed once on a stalled omarchy mirror and passed the clean retry. The date is also
  recorded on the two remaining experimental profiles (`ubuntu-niri`, `arch-omarchy-nvidia`):
  there it certifies one live PASS, not a status promotion. `ubuntu-budgie-24.04` was
  promoted to `unattended` on the strength of this run.
- 2026-09-07: `lubuntu-24.04`, `kubuntu-24.04`, `xubuntu-24.04`, `ubuntu-mate-24.04`, `rocky-9`, `fedora-silverblue`, `opensuse-tumbleweed-autoyast` (superseded above).
- 2026-09-06: the three network-lab profiles and all Windows profiles (the two Windows templates keep this date).

## Remaining work

- Budgie was promoted from `experimental` to `unattended` on 2026-09-09: `verify-desktop`
  reported `Desktop ready: lab has an active local graphical session` on the live matrix, so
  the LightDM autologin drop-in is confirmed and the flavor no longer differs from the other four.
- Revalidate the modified desktop assertions on installed guests, including greetd and OpenRC differences.
- Add a dedicated live/rescue profile for disk and boot repair.
- Consider a small Alpine EFI smoke profile alongside the BIOS baseline.
- Automate a FreeBSD installation to test provisioning assumptions outside Linux.
- Retrieve matching vendor checksums for discovery-selected Alpine/Fedora images; do not attach a static hash to a changing URL.
- Resume interrupted ISO downloads with HTTP Range requests: the Fedora archive cut a 2.4 GB
  transfer at 115 MB and the whole file had to be fetched again.
