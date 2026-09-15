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
- `experimental`: a known incomplete or unsettled flow. Currently the package-only Ubuntu niri recipes, the custom Omarchy/NVIDIA flow and `windows98-unattended`. A full `check-vms` (no profile names) reports them as skipped instead of running them; `check-vms <name>` runs one on purpose, which is how it gets promoted (2026-09-14).

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

## New profiles: batch A (2026-09-13) — all 11 verified live

Every profile below went through `check-vms --clean-first --report` on the host on 2026-09-13/14 and
passed with the desktop (or SSH) checks of its profile. Times are wall-clock in the run that passed.

| Profile | Flow | SSH | Verified | Live | Notes |
|---|---|---|---|---|---|
| `centos-stream-10` | kickstart | 2270 | 2026-09-13 | 5.8 min | server; x86-64-v3 host CPU |
| `fedora-kde` | kickstart | 2260 | 2026-09-13 | 11.4 min | Plasma 6 Wayland; SDDM enabled with `--force` (Plasma Login Manager alias) |
| `fedora-kinoite` | kickstart + ostree | 2261 | 2026-09-13 | 23.9 min | first boot `multi-user`, SDDM layered with `rpm-ostree` over SSH, desktop checked after the reboot |
| `ubuntu-cinnamon-24.04` | autoinstall | 2266 | 2026-09-13 | 11.5 min | `ubuntucinnamon-desktop` |
| `ubuntustudio-24.04` | autoinstall | 2267 | 2026-09-13 | 16.9 min | SDDM, 80G |
| `ubuntu-unity-24.04` | autoinstall | 2265 | 2026-09-13 | 17.4 min | compiz + LightDM |
| `edubuntu-24.04` | autoinstall | 2268 | 2026-09-13 | 24.0 min | includes the 2 min `wait-online` stall (backlog 5b) |
| `debian-gnome` | preseed | 2264 | 2026-09-13 | 53.4 min | netinst desktop download, three Debian installs sharing 1.2 MB/s |
| `debian-xfce` | preseed | 2263 | 2026-09-13 | 58.2 min | same run; 2 minutes under the 3600 s timeout |
| `debian-kde` | preseed | 2262 | 2026-09-14 | 10.4 min | timed out at 30% in the shared run (bandwidth); alone it downloads at 17 MB/s |
| `kali` | preseed | 2269 | 2026-09-14 | 21.4 min | two fixes, below |

Kali needed two things the official `xfce-default.cfg` example does not show. (1) `http.kali.org`
redirects apt to HTTPS mirrors and the freshly debootstrapped target has no `ca-certificates`, so
`pkgsel` died on `certificate verify failed` (error code 100; diagnosed from the installer's syslog
on tty4 via a QMP screenshot): `d-i base-installer/includes string ca-certificates`. (2) Packages of
the default task ask debconf questions inside the target (`kismet-capture-common/install-setuid`
parked the install on a text prompt until the timeout): the 20 answers Kali's own installer ships in
`live-build-config/kali-config/common/includes.installer/preseed.cfg` are now in
`preseed_config.extra`, verbatim.

Bandwidth is the lesson of the Debian trio: with three netinst desktops downloading at once the
host line gave ~1.2 MB/s in total and KDE hit the 3600 s timeout at 30%; alone it finished in 10
minutes. A shared timeout cannot tell a slow mirror from a broken profile: run the netinst desktops
one at a time, or raise `--timeout` when several run together.

Still missing from batch A: `oracle-linux-9` (ISO and SHA-256 verified, profile not written) and
`windows-server-2025` (public evaluation ISO, no vendor checksum for it, `windows.py` knows no
"Server" edition family).

## Backlog (written 2026-09-13 evening, for the next sessions)

Ordered by value over cost. Each item names the evidence behind it.

### The five rows the 2026-09-15 matrix did not pass (first job of the next session)

Full documented run, `artifacts/check-vms/doc-20260915-full/` (92 rows: 45 PASS, 4 FAIL, 1 WARN,
42 SKIP, 19:29-23:24, `--restore --document --parallel auto --timeout 3600`). Screens, timelines and
one PDF sheet per profile are in that directory; the JSON of each row is under `results/`.

1. **`windowsnt4-unattended` (FAIL, timeout 3600 s)** - stalled at the end-of-GUI-stage dialog
   "Installazione di Windows NT 4.00 completata... Riavvia il computer", CPU halted, disk untouched,
   which `NoWaitAfterGUIMode = 1` is supposed to suppress. Same class of stall as row 24 of
   `NT4_PITFALLS.md` (twice at "Configurazione del computer", once under `check-vms` and once under a
   plain bootstrap with 19 GB free). **First thing to test**: whether any keystroke dismisses it - the
   runs that passed had a screenshot recorder sending one Shift every 20 s, the matrix captures
   without touching the guest, so the flow may have always had this defect, masked by that recorder.
   If confirmed, the fix is in the answer file or in a guest-side nudge, not in the matrix.
2. **`centos-stream-10` (FAIL, timeout 3600 s)** - the kickstart never reached
   `==> Kickstart install complete!`; the captured serial shows the installer still alive and talking.
   It passed on 2026-09-13, so it is a regression or a mirror/network problem. Start from the timeline
   frames and the tail of the serial log in the report.
3. **`ubuntu-10.04-unattended` (FAIL, 459 s)** - installed, then the SSH desktop check returned 1
   (the command waiting for `gnome-session|x-session-manag(er)?` and `dpkg-query` on `ubuntu-desktop`).
   Verified live on 2026-09-12, so look at what changed: old-releases mirror, or the 90x2 s wait being
   too short on a loaded host (the row ran while three other guests were installing).
4. **`cachyos-nvidia` (FAIL, 236 s)** - installed, then `~/bin/cachyos-nvidia-post-install` exited 1
   over SSH. The script's own output is in the row's JSON and in the report.
5. **`windows7-unattended` (WARN, 711 s)** - the install finished but the guest agent stayed silent
   ("guest agent silent"), which is the check added for that profile after the 2026-09-07 diagnosis
   (`CLAUDE.md`, the pinned `qemu-ga-win-101.1.0-1.el7ev` MSI). Check whether the MSI still installs
   and whether `QEMU-GA` is running in the guest, before touching the flow.

Also from that run: **an automatic retry for a failed row** was discussed and deliberately not built
yet. The agreed shape, if it is built: one extra attempt (not two, a timeout costs the full hour),
the outcome always stating "passed at attempt 2 of 2" with the first failure's reason, no retry for
deterministic failures (profile check, missing ISO or key), and the VM's artifacts cleaned between
attempts or the second attempt boots the half-installed disk. The single place for it is
`lifecycle.run_local_test_once`, which is already where the row is recorded and its sheet written.

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

5b. **Ubuntu flavors: the installed system waits 1m59s for `systemd-networkd-wait-online`.** Seen on
   the first boot of `edubuntu-24.04` and `ubuntu-unity-24.04` (2026-09-13, `bootstrap-start.log`:
   "Job systemd-networkd-wait-online.service/start running (1min 59s / no limit)"). Ubuntu Server's
   autoinstall leaves networkd enabled and the desktop metapackage brings NetworkManager, so the wait
   never sees a managed link. `lubuntu-lab` already disables the unit in its netlab hook
   (`vmctl/netlab.py`). The fix is a cloud-init `runcmd` line in every flavor profile, but changing a
   profile means re-verifying it live: do it as its own batch over all flavors, with times before/after.

5c. **Rolling repos publish database and signature as two files.** On 2026-09-14 at 00:30 both
   CachyOS profiles died in pacstrap on `cachyos: signature from "CachyOS <admin@cachyos.org>" is
   invalid` (the key was known: the mirror served a database and a signature from two publications,
   both files carry Last-Modified 22:44 GMT of that night); the same ISO, the latest build `260809`,
   installed fine at 06:20. The Arch script now runs `pacman -Syy` with three retries before pacstrap.
   Still open: `check-vms` retries whose install passed left two QEMU processes behind after the
   worker ended (seen 2026-09-14 00:25, no artifacts directory left, 9 GB of RAM held); find which
   step of the retry path skips `cmd_stop`.

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

## New profiles: batches B/C (2026-09-14)

Order after the maintainer's update: FreeBSD, Windows XP/98, then Devuan, Proxmox, Gentoo.

| Profile | SSH | Status | Verified | Live | Evidence / remaining work |
|---|---|---|---|---|---|
| `windowsxp-unattended` | - | unattended | 2026-09-14 | PASS, 8.7 min | Install only (no SSH server on XP). Hands-free from a medium with no boot record: GRUB chainloads SETUPLDR.BIN. SP3 ITA installed from the CD, exit 3010 (reboot pending), confirmed as applied by `winver` after the next boot. Autologon permanent, USB tablet active (`query-mice`: absolute), share as a read-only FAT disk. Product key and ISO are the maintainer's, in `local.json`. |
| `windowsnt4-unattended` | - | unattended | 2026-09-15 | PASS, 28 min (run 10 of 10) | Install only (no SSH server). FreeDOS floppy on the CD runs `WINNT.EXE /U /S /B`; FAT16 disk prepared by the host, kept raw; `\I386\$OEM$` with SP6a as its own .EXE, `CSDVersion = Service Pack 6` read back on COM1; PCnet with `OEMNADAP.IN_` rewritten on the CD (`TP=0`); standard VGA 640x480 (the Cirrus driver spins the first boot); `RunOnce` + `Sermouse` off + `ping` pause so the token reaches COM1; `net user` + `AUTOLOG.REG` so the autologon survives the first boot; `VMCTLOFF.EXE` shuts down to "safe to turn off". Second boot verified: desktop, autologon, no service errors. Guest clock reads the RTC as local time (2 h behind here): untouched. Product key, ISO, SP6a and the Windows 98 ISO for the DOS CD driver are the maintainer's, in `local.json`. |
| `freebsd-unattended` | 2271 | unattended | 2026-09-14 | PASS, 1.5 min | `artifacts/check-vms/20260914-130825-835989/`: clean install, natural shutdown, SSH identity, pgrep -a -x sshd, service status, freebsd-version and sudo. BIOS/UFS server only; EFI and desktop untested. |

Windows XP, what each live run cost (2026-09-14, six runs): the OEM ISO has no El Torito record
at all, so QEMU could not boot it; `grub-mkimage -O i386-pc-eltorito` already contains `cdboot.img`
and concatenating it again produced an image that loads and hangs; a remastered tree from `7z`
(one open error) made Setup stop on a missing `cyclad-z.inf`; `-boot order=dc` restarted Setup for
ever (the CD must sit behind the disk); the standard VGA left XP at 640x480 and the first-logon
"adjust the resolution" dialog blocked a headless guest; `OemSkipWelcome` does not cover msoobe,
only `UnattendSwitch="Yes"` does; `echo ==>` lost its text to cmd's redirection, so the host never
saw a token the guest had printed; the USB tablet needs the builtin UHCI controller because XP has
no xHCI driver; a vvfat share needs `snapshot=on`, since an IDE disk cannot be a read-only block
node; and `AutoLogonCount` makes Winlogon delete the autologon values it was given, so the counter
is removed. Remaining: `windows98-unattended` (MSBATCH.INF), and XP is untested on media that do
carry a boot record.

Windows NT 4.0, what the ten runs of 2026-09-14/15 cost, in order: `$OEM$` at the root is ignored
(Codex's find: it belongs under `\I386`); extracted service-pack files with lower-case names stop the
DOS-side copy; `TimeZone` is the localized display name, not Windows 2000's index; the DEC 21x4 asks
for the cable type and its auto-detection spins the first boot; `BACHSB~1.RM_` renamed by xorriso
without `untranslated_names`; the PCnet INF opens its dialog regardless of unattended mode (rewritten
on the CD, and `TP=0` or the network stage ends in a WinSock error and a `tcpip.sys` stop); `regedit`
is not on the PATH during `CMDLINES.TXT`; `[GuiRunOnce]` left RunOnce empty; `start /wait` returns
nothing on NT 4; the Cirrus driver spins the first boot with and without SP6a; `sermouse.sys` holds
COM1 while RunOnce fires; the blank-password autologon is a one-shot; NT 4 never issues FLUSH CACHE,
so a hard-killed QEMU left a qcow2 whose metadata still described the empty disk (raw now); the ISA
NE2000 installs unattended but its NT 4 driver does not start on QEMU's card. The matrix wrapper
was innocent: a stall at "Configurazione del computer" under `check-vms` coincided with the host
swapping.

FreeBSD manual evidence: `artifacts/freebsd-probe/manual/03-script-install.png` shows
bsdinstall completing the disc1 UFS install; `04-pkg.png` exposed the chroot PATH issue
(`/usr/local/bin` is required). `vendor-detection.txt` confirms the unmodified vendor
startup found an installerconfig graft. The first automated run was continued by hand
only to diagnose it and shut the live guest down naturally; it remains a FAIL.

The second FreeBSD attempt (`artifacts/check-vms/20260914-082403-210577/`, 0.7 min)
failed in pkg DNS after DHCP succeeded: bsdinstall recreated BSDINSTALL_TMPETC and
removed the live resolv.conf symlink target. The preamble now restores the saved
resolver. FAILED and natural shutdown worked; the token is now after the diagnostic
tail so lifecycle's captured output retains it.

Third FreeBSD attempt (`artifacts/check-vms/20260914-082841-231082/`, 1.4 min):
installation and SSH passed; `pgrep -x sshd` falsely failed because FreeBSD excludes
process ancestors, including the listener above the checking SSH session. Live
`pgrep -a -x sshd` and `service sshd onestatus` both returned listener PID 744;
`freebsd-version` returned 14.3-RELEASE and passwordless sudo passed. The profile now
checks both; the clean documented retry is still required.

Validation before the FreeBSD commit: `make check` (687 tests, 225 subtests),
CI-like PATH unittest (687 tests, six existing skips), bootstrap dry run and
`check-vms freebsd-unattended --dry-run` all passed on 2026-09-14. The final clean
retry was deferred after the outside-sandbox pgrep found another session running
`check-vms debian-xfce --document`; that matrix was left untouched.

Final FreeBSD clean PASS on 2026-09-14: 87.963 s (1.5 min),
`artifacts/check-vms/20260914-130825-835989/`, with `--clean-first --timeout 1800
--report --document`. Ran commit `998bd14` from an isolated source snapshot while
preserving another session's uncommitted report changes in the main workspace.
The snapshot shared only the project's ISO cache and artifacts. Installer completed
in under its 120 s shutdown grace; the disk boot passed lab identity, sshd process
and service, FreeBSD 14.3-RELEASE and passwordless sudo, then powered off cleanly.
The report keeps the timeline and English/Italian PDFs. Promoted only after this PASS.
