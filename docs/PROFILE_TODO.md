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
- Non-Linux coverage: `freebsd` (manual), `reactos` (unattend.inf install through `bootstrap-reactos`).
- Ubuntu desktop history: nine manual profiles, one per LTS from 8.04 to 24.04, on the official desktop ISOs (`ubuntu-lts.json`). `ubuntu-8.04-unattended` to `ubuntu-18.04-unattended` install them with `bootstrap-preseed`, `ubuntu-20.04-unattended`/`ubuntu-22.04-unattended` with autoinstall like `ubuntu-gnome-24.04`, on the d-i alternate/server media (live PASS 2026-09-12, see below; 16.04 and 18.04 use the shared `verify-desktop`).
- Canonical names with legacy aliases, safe host-directory migration and pinned CI media with vendor checksums.

## Status semantics

Every tracked profile has `meta.status`:

- `manual`: interactive installation, live media or an import template; automation may still boot or inspect it.
- `unattended`: an automated installation/provisioning recipe exists; this is not a claim that this revision passed a live test.
- `experimental`: a known incomplete or unsettled flow. Currently the package-only Ubuntu niri recipes and the custom Omarchy/NVIDIA flow.

`meta.verified` is the last live PASS date supplied by the maintainer. It is omitted when no date is recorded, and is never updated by unit tests or dry runs. A historical date does not certify subsequent profile changes. The list and HTML report show both fields separately from the current run's PASS/FAIL result.

Recorded dates:

- 2026-09-12: `ubuntu-8.04-unattended`, `ubuntu-10.04-unattended`, `ubuntu-12.04-unattended`, `ubuntu-14.04-unattended`, `ubuntu-16.04-unattended`, `ubuntu-18.04-unattended`, `ubuntu-20.04-unattended`, `ubuntu-22.04-unattended` and `reactos`, each reinstalled from a clean disk with the final recipe (autologin session, passwordless sudo, legacy SSH verified in-guest).
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

## New profiles: batch A (2026-09-13)

- `debian-xfce` (SSH 2263): experimental; bootstrap and matrix dry-runs pass. Live validation deferred on 2026-09-13 because the existing host check-vms matrix (PID 1038972) is running. Reuses preseed without changing the existing flow; no live duration or verified date yet.

- `debian-kde` (SSH 2262): experimental; live validation deferred while the existing host matrix runs (2026-09-13). No live duration or verified date. Debian 13 kde-desktop task with sddm autologin, serial getty and active-session/package/process checks.

- `debian-gnome` (SSH 2264): experimental; live validation deferred while the existing host matrix runs (2026-09-13). No live duration or verified date. Debian 13 gnome-desktop task with gdm3 autologin, serial getty and active-session/package/process checks.

- `ubuntu-unity-24.04` (SSH 2265): experimental; live validation deferred while the existing host matrix runs (2026-09-13). No live duration or verified date. Ubuntu Server 24.04.4 autoinstall with ubuntu-unity-desktop, lightdm autologin and a serial getty. The 40G disk leaves room for desktop packages; verification requires an active local graphical session and compiz.

- `ubuntu-cinnamon-24.04` (SSH 2266): experimental; live validation deferred while the existing host matrix runs (2026-09-13). No live duration or verified date. Ubuntu Server 24.04.4 autoinstall with ubuntucinnamon-desktop, lightdm autologin and a serial getty. The 40G disk leaves room for desktop packages; verification requires an active local graphical session and cinnamon.

- `ubuntustudio-24.04` (SSH 2267): experimental; live validation deferred while the existing host matrix runs (2026-09-13). No live duration or verified date. Ubuntu Server 24.04.4 autoinstall with ubuntustudio-desktop, sddm autologin and a serial getty. The 80G disk leaves room for desktop packages; verification requires an active local graphical session and plasmashell.

- `edubuntu-24.04` (SSH 2268): experimental; live validation deferred while the existing host matrix runs (2026-09-13). No live duration or verified date. Ubuntu Server 24.04.4 autoinstall with edubuntu-desktop, gdm3 autologin and a serial getty. The 60G disk leaves room for desktop packages; verification requires an active local graphical session and gnome-shell.

- `fedora-kde` (SSH 2260): experimental; live validation deferred while the existing host matrix runs (2026-09-13). No live duration or verified date. Plasma 6 Wayland with SDDM autologin; verifies an active local session, plasma-desktop, plasmashell and user-owned kwin_wayland. Compare kubuntu-24.04: Plasma 5.27 X11. Everything netinst uses the Fedora 44 online installation repository.

- `fedora-kinoite` (SSH 2261): experimental; live validation deferred while the existing host matrix runs (2026-09-13). No live duration or verified date. Plasma 6 Wayland with SDDM autologin; verifies an active local session, plasma-desktop, plasmashell and user-owned kwin_wayland. Compare kubuntu-24.04: Plasma 5.27 X11. The ostree ref is read from the ISO; %post only configures. Extra packages use rpm-ostree after installation and activate on reboot.

`fedora-kinoite`: Fedora 44 ships Plasma Login Manager. The first installed boot uses multi-user.target; SSH layers SDDM with rpm-ostree and selects its unit for the next deployment. Desktop checks run after the mandatory reboot. [Fedora 44 login-manager change](https://fedoraproject.org/wiki/Changes/PlasmaLoginManager).

- `kali` (SSH 2269): experimental; live validation deferred while the existing host matrix runs (2026-09-13). No live duration or verified date. Kali netinst preseed uses the vendor Xfce task selection and kali-rolling mirror, LightDM autologin and serial getty. Verification requires kali-desktop-xfce, an active local session and xfce4-session.

- `centos-stream-10` (SSH 2270): experimental; live validation deferred while the existing host matrix runs (2026-09-13). No live duration or verified date. Server-only kickstart from the boot ISO and Stream 10 online repository. Requires an x86-64-v3 host CPU with KVM; QEMU uses the host CPU. SSH and ttyS0 getty are enabled.

## Backlog (written 2026-09-13 evening, for the next sessions)

Ordered by value over cost. Each item names the evidence behind it.

### Flows and scheduler

1. **`bootstrap-unattended` has no timeout on the installer phase and no failure token.** The installer
   runs through `runtime.run()` (`process.wait()` with no limit); `--timeout` only covers SSH and
   post-install. On 2026-09-13 subiquity died in `run_unattended_upgrades` (`unattended-upgrades` exit 1,
   a mirror hiccup) and parked on "An error occurred. Press enter to start a shell": the worker was
   still alive after 1h14 with `--timeout 3600`, and the matrix could not end on its own. Fix in one
   go: (a) apply the timeout to the installer phase, (b) `error-commands` in the autoinstall seed
   that dump `/var/crash/*.crash` to ttyS0, print `==> Ubuntu autoinstall FAILED` and `poweroff -f`,
   (c) detect that token in the installer output *before* booting the disk and waiting for SSH, via
   `lifecycle.explain_failed_bootstrap()`. Covers twelve existing profiles plus the four new flavors.
   Verify like the Arch fix: a real install with a forced failure.
2. **Profiles that will be skipped should not queue for resources.** `windows11-template` (12 GB)
   sat at the head of the queue asking 12 800 MB for a worker that would skip in a second (no ISO),
   holding the Windows 11 install back. Evaluate `local_test_prereq_skip` before scheduling and
   record SKIP at zero cost.
3. **Scheduler: rescan when a grace period expires**, not only on a completion (`concurrent.futures.wait`
   with a timeout equal to the nearest `GRACE_SEC` expiry when jobs are pending). Modest gain, only
   when the live term is the binding one.
4. **Arch mirror robustness.** `arch-noctalia` failed three times on 2026-09-13 on
   `fastly.mirror.pkgbuild.com` ("Operation too slow"). Options: `reflector` in the live system before
   pacstrap, a mirrorlist with several mirrors, or pacman's `--disable-download-timeout`. Measure
   before choosing.
5. **Audio on background VMs**: 57 profiles carry `-device hda-duplex` also when headless, and QEMU
   plays them on the host's speakers (this is why Ubuntu 8.04's login drums are heard during the
   matrix). Decide whether headless runs should get `-audiodev none`; optionally add a sound-card
   check (`aplay -l`) to `verify-desktop`.

### Profiles

6. **Live validation of batch A** (11 profiles, all `experimental`): `vmctl check-vms <names>
   --clean-first --report`, promote each PASS to `unattended` with `meta.verified`. Watch `fedora-kde`
   (SDDM vs Plasma Login Manager) and `fedora-kinoite` (SDDM layered over SSH, desktop checked after
   the reboot) first: they carry the only untested mechanisms.
7. `cachyos-nvidia`: failed three times on 2026-09-13 on `lib32-nvidia-utils` requiring
   `nvidia-utils=610.57.04` not yet in the repo. Upstream skew, nothing to fix here; re-run when
   CachyOS syncs and record the date.
8. Rest of batch A: `oracle-linux-9` (ISO and SHA-256 from `linux.oracle.com/security/gpg/checksum/`,
   verified 2026-09-13), `windows-server-2025` (public evaluation ISO but no vendor checksum found for
   the evaluation build; `install.wim` image name to read with `7z`; `windows.py` knows no "Server"
   edition family and evaluation needs no key — code, not just a profile).
9. Batches B and C of the same plan (Artix, FreeBSD unattended, Devuan, Proxmox VE, Gentoo; Leap 16
   Agama, OpenBSD autoinstall, NetBSD, NixOS unattended, Windows XP/98): the prompt lives in the
   session notes; not started.
10. Ideas not yet planned: the KDE history line (Kubuntu 8.04 → 24.04, KDE 3.5 → Plasma 6) beside the
    GNOME one; sway / i3 / river / labwc as bare compositors; Fedora Sway spin.
