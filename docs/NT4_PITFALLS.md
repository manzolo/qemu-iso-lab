# Windows NT 4.0 unattended: the trap list

Ten live runs on 2026-09-14/15 (plus a handful of control boots on copies of the
installed disks) were needed before `vmctl bootstrap-windowsnt4 windowsnt4-unattended`
went from the DOS floppy to "It is now safe to turn off your computer" without a
keystroke. Every row below cost at least one run. The flow in `vmctl/windowsnt4.py`
already applies every remedy; this page exists so that nobody "simplifies" one away
and pays for it again. Symptoms are quoted as the Italian medium shows them.

| # | Symptom | Cause | Remedy in the flow |
|---|---|---|---|
| 1 | Booting the NT 4 CD starts text-mode Setup, which asks everything | The CD's own boot ignores answer files; unattended NT 4 is `WINNT.EXE /U /S /B` from DOS | The rebuilt ISO boots a FreeDOS 1.3 floppy image (El Torito) that mounts the CD and runs `WINNT.EXE` |
| 2 | `WINNT.EXE` refuses to run from the CD under Windows 98's DOS ("long file name protection") | Win9x DOS blocks direct disk access for programs it does not trust | FreeDOS kernel; only `OAKCDROM.SYS` and `MSCDEX.EXE` come from a 9x startup disk |
| 3 | "Booting from Hard Disk..." hangs before any install | A partitioned, formatted disk with no boot code; BIOS never moves on to the CD | The Windows 98 MBR (chainload the active partition, else `int 0x18`) and `int 0x18` at offset 0x3E of the FAT16 boot sector |
| 4 | Setup copies everything, reboots, hangs at "Booting from Hard Disk..." | `mkfs.vfat --offset` leaves BPB_HiddSec at zero and NT's boot sector computes every address from it | `mkfs.vfat -F 16 -h 2048` |
| 5 | Setup restarts from the beginning at every reboot | `-boot order=dc`: the CD wins over a bootable disk | Disk `bootindex=1`, CD `bootindex=2`; our MBR steps aside only while the disk is not bootable |
| 6 | Text-mode Setup stops on "Impossibile copiare il seguente file: bachsb~1.rmi" | xorriso rewrote `BACHSB~1.RM_` as `BACHSB_1.RM_` (the tilde is not an ISO9660 character) | `-compliance omit_version:untranslated_names` on the rebuild |
| 7 | `CMDLINES.TXT` never runs, SP6a never installs, `CSDVersion` empty | `$OEM$` was at the root of the medium | `$OEM$` lives under `\I386` (Codex's find) |
| 8 | The DOS-side copy stops on the first file of the extracted service pack | Lower-case / long names in `$OEM$`; the copy runs in DOS | Everything in `$OEM$` is 8.3 upper-case; SP6a travels as its own `SP6I386.EXE` (grafted as `NT4SP.EXE`) and runs with `/u /q /z /o` |
| 9 | Date/Time dialog opens with GMT selected | `TimeZone = 110` is a Windows 2000 index; NT 4 wants the display name in the medium's language | The string is read from the vendor's own `\I386\UNATTEND.TXT` on the CD when the profile sets none |
| 10 | "Tipo di connessione" dialog; later the first boot spins at 100 % on a frozen desktop colour | The DEC 21x4 driver (QEMU `tulip`) asks even in unattended mode; its cable auto-detection then loops | Not tulip |
| 11 | "Scheda Ethernet PCI AMD PCNET v3.11: Full duplex / Porta 10Base-T" dialog | `OEMNADAP.INF` never checks `STF_GUI_UNATTENDED` (its ISA twin `OEMNADAM.INF` does) | The `.IN_` cabinet is read, the INF gets a branch after `adapteroptions` (`Set TPValue = 0`, `goto skipoptions`) and is written back stored (`cab_extract_single` / `cab_store_single` / `patch_pcnet_inf`) |
| 12 | "Migrazione da WinSock 1.1 a 2.0 non riuscita", then STOP 0x0A in `tcpip.sys` in the network stage | The patch first kept the INF's internal default `TP=1` (10Base-T forced); the adapter did not start and NT 4.0 RTM's TCP/IP does not survive a bound adapter that fails | `TPValue = 0`, what a confirmed dialog writes (the checkbox is unchecked by default). Same stop with an ISA NE2000 on IRQ 9: adapter parameters must be right the first time |
| 13 | ISA NE2000 (`ne2k_isa`) installs with no dialog but the System log says the driver failed (`%%31`), no network | NT 4's `ne2000.sys` does not recognise QEMU's card | Tracked profile uses `pcnet`; `ne2k_isa` stays accepted for experiments only |
| 14 | Red text in the `CMDLINES.TXT` window: "non è riconosciuto come comando interno o esterno"; no autologon | `%SystemRoot%` is not on the PATH during the GUI stage, so a bare `regedit` is not found | `%SystemRoot%\regedit.exe` |
| 15 | Nothing runs at the first logon; the `RunOnce` key is empty | `[GuiRunOnce]` written the NT 4 way (quoted command per line) is ignored | The first-logon command is a `RunOnce` value imported by `VMCTL.REG` |
| 16 | The service pack's exit code reads 9009 although the hive says Service Pack 6 | NT 4's `start /wait` does not hand back the child's exit code (9009 was the earlier `regedit` failure) | `REPORT.CMD` judges the pack by `CSDVersion` (`regedit /e` + `find`), the exit code is informational |
| 17 | First boot: teal screen, kernel at one address, 100 % CPU, no reaction to Ctrl-Alt-Del | The Cirrus display driver (with and without SP6a) spins on QEMU's cirrus at 800x600x16 | `-vga std`, `[Display]` 640x480 with 4 bpp; `check_profile` rejects cirrus |
| 18 | First boot reaches the desktop, no token on COM1, the `RunOnce` value is consumed | `sermouse.sys` probes COM1 at boot (the `DSs` noise in the serial log) and still holds the port when Explorer runs RunOnce; the redirected command fails silently | `Services\Sermouse Start=4` in the .reg; the RunOnce line has no redirection; `FIRST.CMD` waits (`ping -n 6 127.0.0.1`) and then runs `REPORT.CMD > COM1 2>&1` |
| 19 | Second boot shows "Premere CTRL+ALT+CANC" | Winlogon performs the blank-password automatic logon once and resets `AutoAdminLogon` to 0 | `REPORT.CMD` runs `net user Administrator <admin_password>` and re-imports the autologon with that password (`AUTOLOG.REG`) |
| 20 | No `shutdown.exe`, no WMI, no WSH to end the first logon | NT 4 ships none of them; RUNDLL32 cannot pass ExitWindowsEx its flags | `VMCTLOFF.EXE`, a 1 KB PE written in `vms/profile-files/windowsnt4/exitwin.asm` (`EWX_SHUTDOWN | EWX_FORCE`; with `EWX_POWEROFF` NT 4 rebooted instead) |
| 21 | The guest cannot power the machine off | No APM, no ACPI in NT 4 | The guest stops at "Adesso è possibile spegnere il computer" with everything flushed and `run_and_expect` closes QEMU after `SHUTDOWN_GRACE_SEC` (120 s): the one flow where the host ends the process |
| 22 | After a hard-killed run the disk lists empty directories, `afd.sys` "not MZ", boot hangs; the qcow2 is 470 MB but `qemu-img check` says 4 clusters allocated | NT 4's IDE driver never issues FLUSH CACHE, so qcow2 L1/L2/refcount updates sit in QEMU's cache until a clean exit; a hard kill (timeout, host under memory pressure) keeps only writes into clusters allocated by the format itself (FAT, root) | `disk.format: raw` (enforced), `prepare_disk` writes the raw image directly |
| 23 | A first-boot stop error trashed the FAT16 volume | NT 4 writes the memory dump through the pagefile; with the wrong picture of the disk it lands anywhere | `CrashControl CrashDumpEnabled=0`, `AutoReboot=0`: a stop error stays on the screen for the report's timeline |
| 24 | "Configurazione del computer per l'esecuzione di Windows NT" sits with the CPU halted and the disk untouched, about 16 minutes into the run, until the timeout | Intermittent: twice in about a dozen full installs, the second time under a plain `bootstrap` with 19 GB free on the host, so the `check-vms` wrapper and host memory pressure are both ruled out. The serial log carries one `Slirp: Failed to send packet`, which points at the network stage waiting on something that never arrives | None yet. Kill the run and start it again: the retry passed both times (28-30 min). If it becomes frequent, look at the install-time NIC first |

## More colours: both attempts failed (2026-09-15)

The tracked profile runs the plain VGA driver at 640x480 in 16 colours because NT 4
has nothing better that survives on QEMU. Two alternatives were tried on copies of
the installed disk and on fresh installs; neither works, so do not spend another
evening on them without new information.

| Attempt | What was done | Result |
|---|---|---|
| NT 4's own Cirrus driver at 256 colours | `CIRRUS.SYS` + `CIRRUS.DLL` from the medium, then the SP6a versions, copied into `system32` with the `Services\cirrus\Device0` keys (`InstalledDisplayDrivers = cirrus`, 1024x768, 8 bpp) on a copy of the installed disk, booted with `-vga cirrus` | `STOP c0000143 - File di sistema richiesto DISPLAY_DRIVER.DLL danneggiato o mancante`, both driver versions. The same adapter with the Setup-chosen 800x600x16 spins the kernel at 100 % instead (row 17) |
| VBEMP, the VESA miniport (`vbempk.zip`, bearwindows.zcm.com.au) | Installed the supported unattended way: the three package files plus NT's own expanded `framebuf.dll` in `$OEM$\Textmode`, `[OEMBootFiles]` + `[DisplayDrivers] "AnaPa Corp VBE Miniport" = OEM`, `[Display]` 1024x768x16, `-vga std` | Two fresh installs. Text-mode Setup first refused the package ("Tipi di file non validi o mancanti: sezione Files.Display.VBEMP": its `txtsetup.oem` comments out the `dll` line, and NT 4 wants both file types), and once that was fixed the driver loaded (`vbemp.sys` and `framebuf.dll` both in the module list) and took the first boot down with `STOP 0x1E KMODE_EXCEPTION_NOT_HANDLED` in `win32k.sys`, in the VBE 3.0 variant and in the VBE 2.0 one QEMU's VGA matches |

What was not tried: an older VBEMP release (`vbempg.zip`, 2007), 256 colours through
`vga256.dll` rather than `framebuf.dll`, and NT 4 with a later service pack level at
install time rather than SP6a applied by `CMDLINES.TXT`.

Things that looked like causes and were not: the DOS-side copy (all 1521 files of
`$WIN_NT$.~LS\I386` compared byte for byte with the ISO after a failed run: identical);
the installed `afd.sys` and `tcpip.sys` (identical to the cabinet contents; the "not MZ"
read was a lost-metadata artefact, row 22); the `check-vms` wrapper (row 24).

What remains open: the guest reads the RTC as local time and shows the host's UTC (two
hours behind here); the "Suggerimento" tips window appears at every logon; the display
is plain VGA. None of them stops anything.
