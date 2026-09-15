"""Windows NT 4.0 unattended install: ``UNATTEND.TXT``, a FreeDOS boot floppy on the CD, a FAT16 disk
prepared by the host, and the service pack applied from ``$OEM$`` before the first logon.

NT 4 predates every convenience the later flows lean on. Its CD boots straight into text-mode
Setup, which reads no answer file: the unattended path is ``WINNT.EXE /U /S /B`` from DOS, so the
rebuilt ISO carries a FreeDOS floppy image as its El Torito record (the DOS of a Windows 98 startup
disk refuses to run WINNT.EXE on a CD, "long file name protection", verified live) with a DOS
ATAPI driver and MSCDEX for the CD. WINNT.EXE from DOS copies the whole tree - and, with
``OemPreinstall = yes``, the ``$OEM$`` directory - into ``$WIN_NT$.~LS`` on C:, writes a boot
sector that starts the NT loader, and reboots. C: must exist before that, formatted FAT16 (DOS and
NT 4 read no FAT32), so the host prepares it like the Windows 98 flow does: our own MBR that
steps aside until the partition is bootable, ``mkfs.vfat -F 16 -h 2048``, and ``int 0x18`` where
the dosfstools stub would wait for a key.

Three things about the answer file cost a run each, all verified live on 2026-09-14:

* ``$OEM$`` lives at ``\\I386\\$OEM$`` on the medium, not at the root, or ``CMDLINES.TXT`` never
  runs and the service pack is never applied (Setup reports nothing; ``CSDVersion`` stays empty).
  Everything in it needs an 8.3 upper-case name: Setup copies from DOS, and stopped on the first
  lower-case name of an extracted service pack. The pack therefore travels as its own
  self-extracting ``.EXE`` and is run with ``/u /q /z /o`` (unattended, quiet, no reboot,
  overwrite OEM files); ``UPDATE_EXIT=0`` and ``CSDVersion = Service Pack 6`` were read back from
  the installed hive.
* ``TimeZone`` is the *display name*, in the language of the medium: the numeric indexes belong
  to Windows 2000, and an English string on an Italian medium opens the Date/Time dialog with
  GMT selected. Microsoft's own sample at ``\\I386\\UNATTEND.TXT`` on the CD carries the right
  string, and this module reads it from there when the profile does not name one.
* The network adapter must be one whose in-box driver asks nothing: the DEC 21x4 (QEMU ``tulip``)
  opens "Tipo di connessione" even in unattended mode, and at the first boot its cable
  auto-detection spun the kernel at 100 % with the screen frozen on the desktop colour. The AMD
  PCnet (``pcnet``) is the adapter this flow accepts.

The completion token comes from the first logon: the ``CMDLINES.TXT`` stage writes, with
``regedit /s``, both the autologon and a ``RunOnce`` entry (NT 4's own ``[GuiRunOnce]`` left the
key empty, verified live); NT 4 has no shutdown.exe, WMI or Windows Script
Host, so the script ends with ``VMCTLOFF.EXE`` (``vms/profile-files/windowsnt4/exitwin.asm``, a
1 KB hand-written PE calling ExitWindowsEx). NT 4 cannot power the machine off - no APM, no ACPI;
with EWX_POWEROFF it rebooted - so the guest stops at "It is now safe to turn off your computer"
with everything flushed, and the host closes QEMU after ``SHUTDOWN_GRACE_SEC``.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any

from vmctl import runtime, ui, windows98
from vmctl.errors import VMError


BOOTSTRAP_COMPLETE_TOKEN = "==> Windows NT 4.0 installation complete!"
BOOTSTRAP_FAILED_TOKEN = "==> Windows NT 4.0 installation FAILED"
# After the token the guest shuts down to "safe to turn off" and stays there: QEMU never exits by
# itself on this generation, so run_and_expect terminates it once this grace has passed. An idle
# NT 4 shuts down in well under a minute.
SHUTDOWN_GRACE_SEC = 120
INSTALL_ISO_NAME = "install.iso"
ANSWER_PATH = "/UNATTEND.TXT"
SAMPLE_ANSWER_PATH = "/I386/UNATTEND.TXT"
OEM_DIR_PATH = "/I386/$OEM$"
BOOT_IMAGE_PATH = "/BOOT.IMG"
CD_DRIVE_ID = "nt4cd0"
CD_DEVICE = "MSCD001"
CD_LETTER = "D"
GUEST_DIR = "C:\\VMCTL"
SERVICE_PACK_NAME = "NT4SP.EXE"
SHUTDOWN_TOOL_NAME = "VMCTLOFF.EXE"
CDROM_DRIVER_NAME = "CDROM.SYS"
MSCDEX_NAME = "MSCDEX.EXE"
DEFAULT_FREEDOS_FLOPPY = "isos/freedos-1.3-x86boot.img"
DEFAULT_CDROM_DRIVER = "isos/dos/OAKCDROM.SYS"
DEFAULT_MSCDEX = "isos/dos/MSCDEX.EXE"
PARTITION_START_SECTOR = windows98.PARTITION_START_SECTOR
FAT16_STUB_OFFSET = 0x3E          # where the jump at the start of a FAT12/16 boot sector lands
FAT16_MAX_BYTES = 2 * 1024 ** 3   # DOS and WINNT.EXE address no more than that in one FAT16 volume
SHUTDOWN_TOOL_SOURCE = "vms/profile-files/windowsnt4/exitwin.asm"
# The in-box driver of the AMD PCnet PCI (QEMU ``pcnet``). Its INF, unlike Microsoft's own for the
# ISA twin (OEMNADAM.INF), never looks at STF_GUI_UNATTENDED and opens its parameter dialog ("Full
# duplex", "Porta 10Base-T") in every unattended install; the rebuilt CD carries the INF with the
# lines below added after the ``adapteroptions`` label, so the values a confirmed dialog would
# write go to the registry without a dialog. The .IN_ is a one-file MSZIP cabinet, read and
# rewritten here.
PCNET_INF_NAME = "OEMNADAP.IN_"
PCNET_INF_PATH = f"/I386/{PCNET_INF_NAME}"
PCNET_DIALOG_LABEL = "adapteroptions"
PCNET_SKIP_LABEL = "skipoptions"
# TPValue = 0 is what a confirmed dialog writes: the PCI dialog leaves the "Porta 10Base-T" box
# unchecked (its section sets no CheckItemsIn) and Continue turns that into TP=0 (auto port),
# while the INF's internal default is 1. Left at 1, the first unattended run ended in a
# "WinSock 1.1 to 2.0 migration failed" box and a tcpip.sys IRQL_NOT_LESS_OR_EQUAL stop.
PCNET_UNATTENDED_PATCH = ('    ifstr(i) $(!STF_GUI_UNATTENDED) == "YES"\r\n'
                          '        Set TPValue = 0\r\n'
                          f'        goto {PCNET_SKIP_LABEL}\r\n'
                          '    endif\r\n')
# Assembled from SHUTDOWN_TOOL_SOURCE with: nasm -f bin exitwin.asm -o exitwin.exe
SHUTDOWN_TOOL = bytes.fromhex(
    "4d5a00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "00000000000000000000000040000000504500004c010100000000000000000000000000e00003010b01010000020000"
    "000000000000000000100000001000000010000000004000001000000002000004000000000000000400000000000000"
    "002000000002000000000000030000000000100000100000000010000010000000000000100000000000000000000000"
    "801000005000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000002e74657874000000b3010000001000000002000000020000"
    "000000000000000000000000600000e00000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000068581040006a28ff15dc10400050ff15"
    "f81040006860104000686c1040006a00ff15fc1040006a006a006a00685c1040006a00ff3558104000ff15001140006a"
    "006a05ff151011400085c00f94c00fb6c050ff15e0104000000000000100000000000000000000000200000053655368"
    "7574646f776e50726976696c65676500d010000000000000000000008e110000dc100000e81000000000000000000000"
    "9b110000f8100000081100000000000000000000a8110000101100000000000000000000000000000000000000000000"
    "181100002c11000000000000181100002c110000000000003a1100004e11000066110000000000003a1100004e110000"
    "66110000000000007e110000000000007e11000000000000000047657443757272656e7450726f636573730000004578"
    "697450726f636573730000004f70656e50726f63657373546f6b656e000000004c6f6f6b757050726976696c65676556"
    "616c75654100000041646a757374546f6b656e50726976696c656765730000004578697457696e646f77734578004b45"
    "524e454c33322e444c4c0041445641504933322e444c4c005553455233322e444c4c0000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "00000000000000000000000000000000"
)


def windowsnt4_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("windowsnt4_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid windowsnt4_config: expected object")
    return cfg


def artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "windowsnt4"


def install_iso_path(vm: dict[str, Any]) -> Path:
    return artifact_dir(vm) / INSTALL_ISO_NAME


def _value(value: Any) -> str:
    text = str(value)
    if '"' in text or "\n" in text or "\r" in text:
        raise VMError(f"windowsnt4_config values cannot contain quotes or newlines: {text!r}")
    return text


def _dos_text(lines: list[str]) -> bytes:
    """DOS and NT's cmd read CRLF; a batch file with Unix endings prints ``OFF`` and does nothing."""
    return ("\r\n".join(lines) + "\r\n").encode("cp850")


def _echo(text: str) -> str:
    """A token as ``echo`` must receive it: a bare ``>`` is a redirection, in a batch file too."""
    return text.replace(">", "^>")


def product_key(vm_name: str, vm: dict[str, Any]) -> str:
    """The CD key (``xxx-xxxxxxx``), which a tracked profile must never carry: Setup asks for it."""
    cfg = windowsnt4_config(vm) or {}
    key = str(cfg.get("product_key") or "").strip()
    if not key:
        raise VMError(
            f"Profile '{vm_name}' has no windowsnt4_config.product_key: Setup asks for the CD key in "
            f"its GUI stage and the unattended install would stop there. Put your own key in "
            f"vms/profiles/local.json (gitignored), never in a tracked profile."
        )
    return _value(key)


def service_pack_source(vm: dict[str, Any]) -> Path | None:
    """The self-extracting service pack (``SP6I386.EXE``) to carry in ``$OEM$``, when asked for.

    It is copied as one 8.3-named file and run by ``CMDLINES.TXT``: an extracted tree stopped the
    DOS-side copy on its first lower-case name (verified live).
    """
    cfg = windowsnt4_config(vm) or {}
    source = cfg.get("service_pack")
    if not source:
        return None
    path = runtime.resolve_path(str(source))
    if not path.is_file():
        raise VMError(f"windowsnt4_config.service_pack does not exist: {ui.pretty_path(path)}")
    return path


def dos_pieces(vm: dict[str, Any]) -> tuple[Path, Path, Path]:
    """(FreeDOS boot floppy, DOS ATAPI CD-ROM driver, MSCDEX): the DOS this flow boots from.

    None of the three is on the NT medium. The floppy is ``144m/x86BOOT.img`` from the FreeDOS 1.3
    Floppy Edition; the driver and MSCDEX come from any Windows 9x startup disk, for instance the
    boot image of a Windows 98 CD (``windowsnt4_config.cdrom_driver_iso`` extracts them once).
    """
    cfg = windowsnt4_config(vm) or {}
    floppy = runtime.resolve_path(str(cfg.get("freedos_floppy") or DEFAULT_FREEDOS_FLOPPY))
    driver = runtime.resolve_path(str(cfg.get("cdrom_driver") or DEFAULT_CDROM_DRIVER))
    mscdex = runtime.resolve_path(str(cfg.get("mscdex") or DEFAULT_MSCDEX))
    return floppy, driver, mscdex


def ensure_dos_pieces(vm: dict[str, Any], dry_run: bool = False) -> tuple[Path, Path, Path]:
    """The three DOS files, extracting the CD-ROM driver and MSCDEX from a 9x CD when they are missing."""
    floppy, driver, mscdex = dos_pieces(vm)
    cfg = windowsnt4_config(vm) or {}
    missing = [path for path in (driver, mscdex) if not path.is_file()]
    source = cfg.get("cdrom_driver_iso")
    if missing and source and not dry_run:
        source_iso = runtime.resolve_path(str(source))
        if not source_iso.is_file():
            raise VMError(f"windowsnt4_config.cdrom_driver_iso does not exist: {ui.pretty_path(source_iso)}")
        runtime.require_command("mcopy")
        offset, size = windows98.boot_image_extent(source_iso)
        image = driver.parent / "boot-image.tmp"
        driver.parent.mkdir(parents=True, exist_ok=True)
        with source_iso.open("rb") as handle:
            handle.seek(offset)
            image.write_bytes(handle.read(size))
        try:
            for path in missing:
                # OAKCDROM.SYS / MSCDEX.EXE as they are named on the startup disk
                runtime.run(["mcopy", "-o", "-i", str(image), f"::{path.name}", str(path)], dry_run=False, quiet=True)
        finally:
            image.unlink(missing_ok=True)
        ui.print_status("ok", f"DOS CD-ROM driver and MSCDEX extracted from {ui.pretty_path(source_iso)}")
        missing = [path for path in (driver, mscdex) if not path.is_file()]
    problems = []
    if not floppy.is_file():
        problems.append(f"freedos_floppy {ui.pretty_path(floppy)} (144m/x86BOOT.img of the FreeDOS 1.3 Floppy Edition, "
                        f"https://www.freedos.org/download/)")
    for path in missing:
        problems.append(f"{ui.pretty_path(path)} (from a Windows 9x startup disk: set windowsnt4_config.cdrom_driver_iso "
                        f"to a Windows 98 ISO and it is extracted for you)")
    if problems and not dry_run:
        raise VMError("The DOS side of the Windows NT 4.0 install is missing: " + "; ".join(problems))
    return floppy, driver, mscdex


def read_sample_timezone(source_iso: Path) -> str | None:
    """The ``TimeZone`` line of Microsoft's own ``\\I386\\UNATTEND.TXT`` on the medium.

    NT 4 matches the value against the display names of its own language, so the sample the
    vendor ships on each localized CD is the one place that spells it right.
    """
    if not source_iso.is_file():
        return None
    with tempfile.TemporaryDirectory(prefix="vmctl-nt4-") as tmp:
        sample = Path(tmp) / "UNATTEND.TXT"
        result = subprocess.run(["xorriso", "-osirrox", "on", "-indev", str(source_iso),
                                 "-extract", SAMPLE_ANSWER_PATH, str(sample)],
                                capture_output=True, check=False)
        if result.returncode != 0 or not sample.is_file():
            return None
        text = sample.read_bytes().decode("cp1252", errors="replace")
    for line in text.splitlines():
        match = re.match(r'\s*TimeZone\s*=\s*"?([^"]+?)"?\s*$', line, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def timezone(vm_name: str, vm: dict[str, Any], source_iso: Path | None) -> str:
    cfg = windowsnt4_config(vm) or {}
    explicit = cfg.get("timezone")
    if explicit:
        return _value(explicit)
    sample = read_sample_timezone(source_iso) if source_iso is not None else None
    if sample:
        return _value(sample)
    if source_iso is not None and not source_iso.is_file():
        # a dry run without the medium: the placeholder shows the shape of what is expected
        return "(GMT+01:00) Berlino, Stoccolma, Roma, Berna, Bruxelles, Vienna"
    raise VMError(
        f"Profile '{vm_name}': windowsnt4_config.timezone is not set and the medium carries no sample "
        f"{SAMPLE_ANSWER_PATH}. NT 4 wants the display name in the medium's own language, e.g. "
        f'"(GMT+01:00) Berlino, Stoccolma, Roma, Berna, Bruxelles, Vienna" on an Italian CD.'
    )


NE2000_DEFAULT_IOBASE = 0x300
NE2000_DEFAULT_IRQ = 5


def network_adapter(vm: dict[str, Any]) -> str:
    """The QEMU device name of the profile's NIC, without its options."""
    return str(vm.get("network_device", "")).split(",", 1)[0]


def ne2000_parameters(vm: dict[str, Any]) -> tuple[int, int]:
    """(I/O base, IRQ) of the ISA NE2000, read from the device options so the answer file matches QEMU."""
    options = dict(part.split("=", 1) for part in str(vm.get("network_device", "")).split(",")[1:] if "=" in part)
    return int(options.get("iobase", str(NE2000_DEFAULT_IOBASE)), 0), int(options.get("irq", str(NE2000_DEFAULT_IRQ)), 0)


def render_network_sections(vm: dict[str, Any]) -> list[str]:
    """[Network] and what it points at: an installed ISA NE2000 with its parameters, or detection.

    The NE2000 INF (OEMNADN2.INF) honours STF_GUI_UNATTENDED and takes IRQ and I/O base from the
    adapter section, so nothing asks a question - but at runtime NT 4's ne2000.sys reports QEMU's
    ne2k_isa as "not functioning" (event %%31, verified live) and the guest has no network. The
    PCnet path (detection, patched INF) is the one the tracked profile uses.
    """
    cfg = windowsnt4_config(vm) or {}
    lines = ["[Network]"]
    if network_adapter(vm) == "ne2k_isa":
        iobase, irq = ne2000_parameters(vm)
        lines += ["InstallAdapters = AdaptersSection"]
    else:
        # detect the one adapter there is; the profile check keeps it one that asks nothing
        lines += ['DetectAdapters = ""']
    lines += [
        "InstallProtocols = ProtocolsSection",
        f"JoinWorkgroup = {_value(cfg.get('workgroup', 'WORKGROUP'))}",
        "",
    ]
    if network_adapter(vm) == "ne2k_isa":
        lines += [
            "[AdaptersSection]",
            "NE2000 = NE2000Params",
            "",
            "[NE2000Params]",
            f"InterruptNumber = {irq}",
            f"IOBaseAddress = {iobase}",
            "BusType = 1",
            "",
        ]
    lines += [
        "[ProtocolsSection]",
        "TC = TCParameters",
        "",
        "[TCParameters]",
        "DHCP = yes",
    ]
    return lines


def first_logon_command() -> str:
    """The RunOnce entry: the launcher CMDLINES.TXT copied to C:. No redirection here: COM1 is
    opened inside FIRST.CMD, after a pause, once the boot-time port probing is over."""
    return f"cmd /c {GUEST_DIR}\\FIRST.CMD"


def render_launcher_script() -> bytes:
    """``FIRST.CMD``: wait a few seconds (ping is the only sleep NT 4 has), then report on COM1."""
    return _dos_text([
        "@echo off",
        "ping -n 6 127.0.0.1 > nul",
        f"call {GUEST_DIR}\\REPORT.CMD > COM1 2>&1",
    ])


def render_unattend(vm_name: str, vm: dict[str, Any], source_iso: Path | None = None) -> str:
    """The answer file WINNT.EXE reads with ``/U``: text stage, GUI stage, network, first logon."""
    cfg = windowsnt4_config(vm)
    if cfg is None:
        raise VMError(f"Profile '{vm_name}' has no windowsnt4_config section")
    display_cfg = cfg.get("display")
    display: dict[str, Any] = display_cfg if isinstance(display_cfg, dict) else {}
    lines = [
        "; Rendered by vmctl: Windows NT 4.0 unattended answer file.",
        "[Unattended]",
        # $OEM$ is copied and CMDLINES.TXT runs only with this
        "OemPreinstall = yes",
        "OemSkipEula = yes",
        # without these two, Setup stops at the end of each stage asking for Enter
        "NoWaitAfterTextMode = 1",
        "NoWaitAfterGUIMode = 1",
        "ConfirmHardware = no",
        "NtUpgrade = no",
        "Win31Upgrade = no",
        f"TargetPath = {_value(cfg.get('target_path', 'WINNT'))}",
        "OverwriteOemFilesOnUpgrade = no",
        # the host made C: already; converting it would add a reboot and NTFS 4 gains us nothing
        "FileSystem = LeaveAlone",
        "ExtendOemPartition = 0",
        "",
        "[UserData]",
        f'FullName = "{_value(cfg.get("full_name", "Lab User"))}"',
        f'OrgName = "{_value(cfg.get("organization", "qemu-iso-lab"))}"',
        f"ComputerName = {_value(cfg.get('computer_name', 'NT4-LAB'))}",
        f'ProductID = "{product_key(vm_name, vm)}"',
        "",
        "[GuiUnattended]",
        "OemSkipWelcome = 1",
        # Administrator with no password; the autologon values written by CMDLINES.TXT match it
        "OEMBlankAdminPassword = 1",
        f'TimeZone = "{timezone(vm_name, vm, source_iso)}"',
        "",
        # The mode of the plain VGA driver. The Cirrus driver would give more, and QEMU has that
        # adapter, but after SP6a the first boot spins forever inside it at 800x600x16 (kernel at one
        # address, 100 % CPU, the screen frozen on the desktop colour; the same disk booted with the
        # standard VGA reached the logon screen in a minute - verified live 2026-09-14).
        "[Display]",
        "ConfigureAtLogon = 0",
        f"BitsPerPel = {int(display.get('BitsPerPel', 4))}",
        f"XResolution = {int(display.get('XResolution', 640))}",
        f"YResolution = {int(display.get('YResolution', 480))}",
        f"VRefresh = {int(display.get('VRefresh', 60))}",
        "AutoConfirm = 1",
        "",
        *render_network_sections(vm),
        # No [GuiRunOnce]: written the NT 4 way ("command" per line) it left the RunOnce key empty
        # and nothing ran at the first logon (verified live, hive read offline). The first-logon
        # command goes into RunOnce through VMCTL.REG in the CMDLINES.TXT stage instead.
    ]
    return "\n".join(lines) + "\n"


def render_cmdlines() -> bytes:
    """``CMDLINES.TXT``: what Setup runs from ``$OEM$`` at the end of its GUI stage."""
    return _dos_text(["[Commands]", '".\\VMCTL.CMD"'])


def render_registry(vm: dict[str, Any]) -> bytes:
    """Autologon for the blank-password Administrator and the first-logon RunOnce entry, as a .reg."""
    cfg = windowsnt4_config(vm) or {}
    return _dos_text([
        "REGEDIT4",
        "",
        "[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon]",
        '"AutoAdminLogon"="1"',
        '"DefaultUserName"="Administrator"',
        f'"DefaultDomainName"="{_value(cfg.get("computer_name", "NT4-LAB"))}"',
        '"DefaultPassword"=""',
        "",
        "[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce]",
        f'"vmctl"="{first_logon_command().replace(chr(92), chr(92) * 2)}"',
        "",
        # A stop error must stay on the screen, where the report's timeline captures it, and must
        # not write a memory dump: the dump of a first-boot crash went through the pagefile and
        # left the FAT16 volume with empty directories and an unbootable C: (verified live).
        "[HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control\\CrashControl]",
        '"CrashDumpEnabled"=dword:00000000',
        '"AutoReboot"=dword:00000000',
        "",
        # No serial mouse detection: at every boot sermouse.sys probes COM1 (the "DSs" noise in the
        # serial log) and holds the port while Explorer runs RunOnce, so a first-logon command
        # redirected to COM1 failed silently and its RunOnce value was consumed (verified live:
        # the same script run by hand a minute later printed everything). The pointer is PS/2.
        "[HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\Sermouse]",
        '"Start"=dword:00000004',
    ])


def render_autologon_registry(vm: dict[str, Any]) -> bytes:
    """``AUTOLOG.REG``: the autologon again, with the password REPORT.CMD has just set.

    Winlogon performs an automatic logon with an empty DefaultPassword exactly once and then
    resets AutoAdminLogon to 0 (the second boot showed the Ctrl-Alt-Del prompt, verified live),
    so the first logon gives Administrator a password and re-imports the values with it.
    """
    cfg = windowsnt4_config(vm) or {}
    return _dos_text([
        "REGEDIT4",
        "",
        "[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon]",
        '"AutoAdminLogon"="1"',
        '"DefaultUserName"="Administrator"',
        f'"DefaultDomainName"="{_value(cfg.get("computer_name", "NT4-LAB"))}"',
        f'"DefaultPassword"="{_value(cfg.get("admin_password", "lab"))}"',
    ])


def render_setup_script(vm: dict[str, Any]) -> bytes:
    """``VMCTL.CMD``, run by CMDLINES.TXT with ``$OEM$`` as the current directory.

    Setup deletes ``$WIN_NT$.~LS`` when it finishes, so what the first logon needs is copied to
    ``C:\\VMCTL`` first; the service pack runs here, where Microsoft's KB puts it, and its exit code
    is kept for the first-logon script to report.
    """
    lines = [
        "@echo off",
        f"md {GUEST_DIR}",
        f"copy .\\FIRST.CMD {GUEST_DIR}\\ > nul",
        f"copy .\\REPORT.CMD {GUEST_DIR}\\ > nul",
        f"copy .\\AUTOLOG.REG {GUEST_DIR}\\ > nul",
        f"copy .\\{SHUTDOWN_TOOL_NAME} {GUEST_DIR}\\ > nul",
        # full path: during the GUI stage %SystemRoot% is not on the PATH yet, and a bare "regedit"
        # came back as "non e' riconosciuto come comando interno o esterno" (verified live)
        "%SystemRoot%\\regedit.exe /s .\\VMCTL.REG",
    ]
    if service_pack_source(vm) is not None:
        lines += [
            # start /wait: update.exe hands over to a child, and Setup would otherwise reboot
            # under it. /u unattended, /q quiet, /z no reboot, /o overwrite OEM files unasked.
            f"start /wait .\\{SERVICE_PACK_NAME} /u /q /z /o",
            # informational only: NT 4's start /wait does not hand back the child's exit code
            # (the log read 9009, the code of the regedit that had failed before it, while the
            # hive said Service Pack 6 - verified live); the first logon checks CSDVersion instead
            f"echo UPDATE_EXIT=%ERRORLEVEL%> {GUEST_DIR}\\SP.LOG",
        ]
    return _dos_text(lines)


def render_first_logon_script(vm: dict[str, Any]) -> bytes:
    """``REPORT.CMD``: evidence to COM1, the profile's commands, the token, the shutdown."""
    cfg = windowsnt4_config(vm) or {}
    lines = [
        "@echo off",
        "echo ==^> vmctl: first logon",
        "ver",
    ]
    lines += [
        # the service pack level, exported with the one registry tool NT 4 has (ANSI REGEDIT4)
        f'regedit /e {GUEST_DIR}\\VER.REG "HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion"',
        f'find "CSDVersion" {GUEST_DIR}\\VER.REG',
    ]
    if service_pack_source(vm) is not None:
        lines += [
            f"type {GUEST_DIR}\\SP.LOG",
            # CSDVersion is what the pack writes; its exit code never reaches this script on NT 4
            f'find "Service Pack" {GUEST_DIR}\\VER.REG > nul',
            f"if errorlevel 1 echo {_echo(BOOTSTRAP_FAILED_TOKEN)}: CSDVersion shows no service pack",
            "if errorlevel 1 goto off",
        ]
    lines += [
        # the blank-password autologon is a one-shot: give the account its password and make it stay
        f"net user Administrator {_value(cfg.get('admin_password', 'lab'))}",
        f"%SystemRoot%\\regedit.exe /s {GUEST_DIR}\\AUTOLOG.REG",
        "echo ==^> vmctl: autologon set for Administrator",
    ]
    lines += [str(command) for command in cfg.get("setup_commands", [])]
    lines += [
        f"echo {_echo(BOOTSTRAP_COMPLETE_TOKEN)}",
        ":off",
        f"{GUEST_DIR}\\{SHUTDOWN_TOOL_NAME}",
        "echo ==^> vmctl: shutdown tool returned %ERRORLEVEL%",
    ]
    return _dos_text(lines)


def render_fdconfig() -> bytes:
    """FreeDOS ``FDCONFIG.SYS``: the CD-ROM driver, then a shell that runs our batch file."""
    return _dos_text([
        "LASTDRIVE=Z",
        "BUFFERS=20",
        "FILES=40",
        f"DEVICE=A:\\{CDROM_DRIVER_NAME} /D:{CD_DEVICE}",
        "SHELL=\\FREEDOS\\BIN\\COMMAND.COM \\FREEDOS\\BIN /E:1024 /P=A:\\FDAUTO.BAT",
    ])


def render_fdauto(vm: dict[str, Any]) -> bytes:
    """FreeDOS ``FDAUTO.BAT``: mount the CD as D:, start WINNT.EXE with the answer file."""
    return _dos_text([
        "@ECHO OFF",
        "SET PATH=A:\\;A:\\FREEDOS\\BIN",
        f"A:\\{MSCDEX_NAME} /D:{CD_DEVICE} /L:{CD_LETTER}",
        f"IF NOT EXIST {CD_LETTER}:\\I386\\WINNT.EXE GOTO NOCD",
        "ECHO vmctl: Windows NT 4.0 Setup from the CD",
        f"{CD_LETTER}:",
        "CD \\I386",
        # /U answer file, /S source, /B no boot floppies: the loader goes on C:
        f"WINNT.EXE /U:{CD_LETTER}:\\UNATTEND.TXT /S:{CD_LETTER}:\\I386 /B",
        "GOTO END",
        ":NOCD",
        "ECHO vmctl: no CD-ROM on D: - the DOS driver did not find the drive",
        ":END",
    ])


def cab_extract_single(data: bytes) -> tuple[str, bytes]:
    """(name, contents) of the one file in a cabinet, stored or MSZIP: what NT 4's .IN_/.EX_ are."""
    if data[:4] != b"MSCF":
        raise VMError("Not a cabinet file (no MSCF signature)")
    coff_files, = struct.unpack_from("<I", data, 16)
    folders, files, flags = struct.unpack_from("<HHH", data, 26)
    if folders != 1 or files != 1 or flags & 0x7:
        raise VMError(f"Unsupported cabinet: {folders} folder(s), {files} file(s), flags {flags:#x}")
    coff_data, blocks, compression = struct.unpack_from("<IHH", data, 36)
    compression &= 0x0F
    if compression not in (0, 1):
        raise VMError(f"Unsupported cabinet compression type {compression}")
    size, = struct.unpack_from("<I", data, coff_files)
    name_start = coff_files + 16
    name = data[name_start:data.index(b"\0", name_start)].decode("ascii", errors="replace")
    out = bytearray()
    offset = coff_data
    for _ in range(blocks):
        _checksum, packed, unpacked = struct.unpack_from("<IHH", data, offset)
        chunk = data[offset + 8:offset + 8 + packed]
        if compression == 1:
            if chunk[:2] != b"CK":
                raise VMError("Malformed MSZIP block in cabinet")
            # every block is its own deflate stream, but the LZ77 window carries over from the
            # previous block's output
            inflater = zlib.decompressobj(-15, zdict=bytes(out[-32768:])) if out else zlib.decompressobj(-15)
            chunk = inflater.decompress(chunk[2:]) + inflater.flush()
        if len(chunk) != unpacked:
            raise VMError(f"Cabinet block decoded to {len(chunk)} bytes, expected {unpacked}")
        out += chunk
        offset += 8 + packed
    return name, bytes(out[:size])


def cab_store_single(name: str, payload: bytes, date: int = 0x2122, time_: int = 0x0000) -> bytes:
    """A one-file cabinet holding *payload* uncompressed: valid for Setup, no compressor needed."""
    blocks = [payload[i:i + 32768] for i in range(0, len(payload), 32768)] or [b""]
    encoded_name = name.encode("ascii") + b"\0"
    header_size, folder_size = 36, 8
    file_size = 16 + len(encoded_name)
    coff_files = header_size + folder_size
    coff_data = coff_files + file_size
    body = b"".join(struct.pack("<IHH", 0, len(block), len(block)) + block for block in blocks)
    total = coff_data + len(body)
    header = b"MSCF" + struct.pack("<IIIIIBBHHHHH", 0, total, 0, coff_files, 0, 3, 1, 1, 1, 0, 0x1234, 0)
    folder = struct.pack("<IHH", coff_data, len(blocks), 0)
    file_entry = struct.pack("<IIHHHH", len(payload), 0, 0, date, time_, 0x20) + encoded_name
    return header + folder + file_entry + body


def patch_pcnet_inf(original: bytes) -> bytes:
    """The vendor's PCnet INF with the unattended branch its ISA twin already has."""
    name, text_bytes = cab_extract_single(original)
    text = text_bytes.decode("cp1252")
    if "STF_GUI_UNATTENDED" in text:
        return original
    lines = text.split("\n")
    labels = [i for i, line in enumerate(lines) if line.strip().rstrip("+").strip().rstrip("=").strip() == PCNET_DIALOG_LABEL
              and "=" in line]
    if len(labels) != 1 or not any(line.strip().startswith(PCNET_SKIP_LABEL) for line in lines):
        raise VMError(f"{PCNET_INF_NAME} on this medium has no '{PCNET_DIALOG_LABEL}'/'{PCNET_SKIP_LABEL}' labels: "
                      f"set windowsnt4_config.patch_pcnet_inf to false and answer the adapter dialog by hand")
    # the vendor file is CRLF: every inserted line ends the same way, or the INF parser stumbles
    eol = "\r" if "\r\n" in text else ""
    for offset, line in enumerate(PCNET_UNATTENDED_PATCH.replace("\r\n", "\n").rstrip("\n").split("\n")):
        lines.insert(labels[0] + 1 + offset, line + eol)
    patched = "\n".join(lines).encode("cp1252")
    return cab_store_single(name, patched)


def pcnet_patch_wanted(vm: dict[str, Any]) -> bool:
    cfg = windowsnt4_config(vm) or {}
    return cfg.get("patch_pcnet_inf", True) is not False and str(vm.get("network_device", "")) == "pcnet"


def pcnet_inf_override(vm: dict[str, Any], source_iso: Path, work: Path, dry_run: bool = False) -> Path | None:
    """The patched OEMNADAP.IN_ to map over the medium's own, or None when the profile opts out."""
    if not pcnet_patch_wanted(vm):
        return None
    target = work / PCNET_INF_NAME
    if dry_run:
        return target
    original = work / (PCNET_INF_NAME + ".orig")
    result = subprocess.run(["xorriso", "-osirrox", "on", "-indev", str(source_iso),
                             "-extract", PCNET_INF_PATH, str(original)], capture_output=True, check=False)
    if result.returncode != 0 or not original.is_file():
        raise VMError(f"{PCNET_INF_PATH} not found on {ui.pretty_path(source_iso)}: not an NT 4.0 x86 medium?")
    target.write_bytes(patch_pcnet_inf(original.read_bytes()))
    original.unlink()
    return target


def prepare_disk(vm: dict[str, Any], dry_run: bool = False) -> Path:
    """A disk WINNT.EXE can copy to: one active FAT16 partition, boot code that steps aside.

    Same construction as the Windows 98 flow, FAT16 instead of FAT32 (neither DOS nor NT 4 reads
    FAT32) and ``-h`` for BPB_HiddSec, which ``mkfs.vfat --offset`` otherwise leaves at zero and
    the NT boot sector then computes every address from.
    """
    disk = runtime.resolve_path(vm["disk"]["path"])
    size = str(vm["disk"].get("size", "2G"))
    sectors = _size_in_sectors(size)
    # A raw image, kept as such: NT 4's IDE driver never issues FLUSH CACHE, so with qcow2 the
    # L1/L2/refcount updates sit in QEMU's cache until a clean exit, and two runs that ended in a
    # hard kill (timeout, host under memory pressure) left a 470 MB file whose metadata still
    # described the empty disk - every cluster allocated after the format read back as zeros.
    raw_format = str(vm["disk"].get("format", "qcow2")) == "raw"
    raw = disk if raw_format else disk.with_suffix(".raw")
    ui.print_kv("disk", f"{ui.pretty_path(disk)} ({size}, FAT16, prepared by the host)")
    if dry_run:
        ui.print_status("ok", "Would prepare the FAT16 disk and its boot code")
        return disk
    runtime.require_command("mkfs.vfat")
    if not raw_format:
        runtime.require_command("qemu-img")
    disk.parent.mkdir(parents=True, exist_ok=True)
    with raw.open("wb") as handle:
        handle.truncate(sectors * 512)
    sector = bytearray(512)
    sector[:len(windows98.MBR_CODE)] = windows98.MBR_CODE
    entry = bytearray(16)
    entry[0] = 0x80                                   # active
    entry[1:4] = bytes([0xFE, 0xFF, 0xFF])            # CHS is meaningless here; the MBR reads LBA
    entry[4] = 0x06                                   # FAT16
    entry[5:8] = bytes([0xFE, 0xFF, 0xFF])
    entry[8:12] = struct.pack("<I", PARTITION_START_SECTOR)
    entry[12:16] = struct.pack("<I", sectors - PARTITION_START_SECTOR)
    sector[446:462] = entry
    sector[510:512] = b"\x55\xaa"
    with raw.open("r+b") as handle:
        handle.write(bytes(sector))
    runtime.run(["mkfs.vfat", "-F", "16", "-h", str(PARTITION_START_SECTOR), "--offset", str(PARTITION_START_SECTOR),
                 "-n", "NT4", str(raw), str((sectors - PARTITION_START_SECTOR) // 2)],
                dry_run=False, quiet=True)
    with raw.open("r+b") as handle:
        handle.seek(PARTITION_START_SECTOR * 512 + FAT16_STUB_OFFSET)
        handle.write(windows98.RETURN_TO_BIOS)
    if not raw_format:
        if disk.exists():
            disk.unlink()
        runtime.run(["qemu-img", "convert", "-O", "qcow2", str(raw), str(disk)], dry_run=False, quiet=True)
        raw.unlink()
    ui.print_status("ok", f"Disk ready for WINNT.EXE: {ui.pretty_path(disk)}")
    return disk


def _size_in_sectors(size: str) -> int:
    text = size.strip().upper()
    multipliers = {"G": 1024 ** 3, "M": 1024 ** 2, "K": 1024}
    factor = multipliers.get(text[-1:], 1)
    number = text[:-1] if text[-1:] in multipliers else text
    try:
        total = int(float(number) * factor)
    except ValueError as exc:
        raise VMError(f"Invalid disk.size for a Windows NT 4.0 profile: {size!r}") from exc
    if total < 512 * 1024 * 1024:
        raise VMError(f"disk.size {size!r} is too small for Windows NT 4.0 (512M is the floor)")
    if total > FAT16_MAX_BYTES:
        raise VMError(f"disk.size {size!r} is too large: WINNT.EXE runs from DOS and copies to a FAT16 volume, 2G at most")
    return total // 512


def build_boot_floppy(vm: dict[str, Any], work: Path, dry_run: bool = False) -> Path:
    """The FreeDOS floppy with the CD-ROM driver, MSCDEX and startup files that run Setup."""
    target = work / "BOOT.IMG"
    floppy, driver, mscdex = ensure_dos_pieces(vm, dry_run=dry_run)
    if dry_run:
        return target
    runtime.require_command("mcopy")
    shutil.copyfile(floppy, target)
    listing = subprocess.run(["mdir", "-i", str(target), "::/FREEDOS/BIN/COMMAND.COM"],
                             capture_output=True, text=True, check=False)
    if listing.returncode != 0:
        raise VMError(f"Not a FreeDOS boot floppy: {ui.pretty_path(floppy)} has no \\FREEDOS\\BIN\\COMMAND.COM")
    (work / "FDCONFIG.SYS").write_bytes(render_fdconfig())
    (work / "FDAUTO.BAT").write_bytes(render_fdauto(vm))
    for source, name in ((driver, CDROM_DRIVER_NAME), (mscdex, MSCDEX_NAME),
                         (work / "FDCONFIG.SYS", "FDCONFIG.SYS"), (work / "FDAUTO.BAT", "FDAUTO.BAT")):
        runtime.run(["mcopy", "-o", "-i", str(target), str(source), f"::{name}"], dry_run=False, quiet=True)
    return target


def oem_files(vm: dict[str, Any]) -> dict[str, bytes]:
    """What goes into ``$OEM$``, all 8.3 upper-case: Setup copies it from DOS."""
    return {
        "CMDLINES.TXT": render_cmdlines(),
        "VMCTL.CMD": render_setup_script(vm),
        "FIRST.CMD": render_launcher_script(),
        "REPORT.CMD": render_first_logon_script(vm),
        "VMCTL.REG": render_registry(vm),
        "AUTOLOG.REG": render_autologon_registry(vm),
        SHUTDOWN_TOOL_NAME: SHUTDOWN_TOOL,
    }


def _stamp(source_iso: Path, answer: str, vm: dict[str, Any]) -> str:
    st = source_iso.stat()
    digest = hashlib.sha256(answer.encode("cp1252") + b"".join(oem_files(vm).values())
                            + render_fdconfig() + render_fdauto(vm)
                            + (PCNET_UNATTENDED_PATCH.encode() if pcnet_patch_wanted(vm) else b"")).hexdigest()
    service_pack = service_pack_source(vm)
    return f"{source_iso.resolve()}\n{st.st_size}\n{st.st_mtime_ns}\n{digest}\n{service_pack}\n"


def ensure_install_iso(vm_name: str, vm: dict[str, Any], source_iso: Path, answer: str, dry_run: bool = False) -> Path:
    """The per-VM install CD: the FreeDOS boot floppy, the answer file, ``\\I386\\$OEM$``."""
    dest = install_iso_path(vm)
    stamp_path = dest.with_name(dest.name + ".source")
    stamp = _stamp(source_iso, answer, vm) if source_iso.is_file() else None
    if dest.is_file() and stamp is not None and stamp_path.is_file() and stamp_path.read_text(encoding="utf-8") == stamp:
        ui.print_status("ok", f"Unattended Windows NT 4.0 ISO ready: {ui.pretty_path(dest)}")
        return dest
    runtime.require_command("xorriso")
    ui.print_header("Build the unattended Windows NT 4.0 ISO")
    ui.print_kv("source", ui.pretty_path(source_iso))
    ui.print_kv("target", ui.pretty_path(dest))
    work = artifact_dir(vm) / "iso-work"
    if work.exists() and not dry_run:
        shutil.rmtree(work)
    oem = work / "oem"
    if not dry_run:
        oem.mkdir(parents=True, exist_ok=True)
        (work / "UNATTEND.TXT").write_bytes(answer.replace("\n", "\r\n").encode("cp1252"))
        for name, data in oem_files(vm).items():
            (oem / name).write_bytes(data)
    boot_image = build_boot_floppy(vm, work, dry_run=dry_run)
    pcnet_inf = pcnet_inf_override(vm, source_iso, work, dry_run=dry_run)
    service_pack = service_pack_source(vm)
    # xorriso refuses to overwrite an existing -outdev and leaves the old image in place
    partial = dest.with_name(dest.name + ".part")
    if not dry_run and partial.exists():
        partial.unlink()
    command = ["xorriso", "-indev", str(source_iso), "-outdev", str(partial),
               # The medium's own El Torito image (text-mode Setup, which reads no answer file) is
               # dropped: the CD boots our DOS floppy instead. The compliance pair is the one the
               # XP/2000 flow pays for: without untranslated_names xorriso rewrites BACHSB~1.RM_ as
               # BACHSB_1.RM_ (the tilde is not an ISO9660 character) and text-mode Setup stops on
               # "Impossibile copiare il seguente file: bachsb~1.rmi" (verified live on this medium).
               "-compliance", "omit_version:untranslated_names",
               "-map", str(work / "UNATTEND.TXT"), ANSWER_PATH,
               "-map", str(oem), OEM_DIR_PATH]
    if service_pack is not None:
        ui.print_kv("service pack", ui.pretty_path(service_pack))
        command += ["-map", str(service_pack), f"{OEM_DIR_PATH}/{SERVICE_PACK_NAME}"]
    if pcnet_inf is not None:
        ui.print_kv("network driver", "OEMNADAP.IN_ with the unattended branch (no parameter dialog)")
        command += ["-map", str(pcnet_inf), PCNET_INF_PATH]
    command += ["-map", str(boot_image), BOOT_IMAGE_PATH,
                "-boot_image", "any", f"bin_path={BOOT_IMAGE_PATH}",
                "-boot_image", "any", "emul_type=diskette",
                "-commit"]
    runtime.run(command, dry_run=dry_run, quiet=True)
    if not dry_run:
        partial.replace(dest)
        shutil.rmtree(work, ignore_errors=True)
        if stamp is not None:
            stamp_path.write_text(stamp, encoding="utf-8")
    ui.print_status("ok", f"Unattended Windows NT 4.0 ISO: {ui.pretty_path(dest)}")
    return dest


def install_media_args(iso_path: Path) -> list[str]:
    """The installer CD behind the disk: our MBR steps aside until WINNT.EXE has made C: bootable."""
    return [
        "-drive", f"id={CD_DRIVE_ID},file={iso_path},format=raw,if=none,media=cdrom,readonly=on",
        "-device", f"ide-cd,drive={CD_DRIVE_ID},bus=ide.1,bootindex=2",
    ]


def headless_video_args(vm: dict[str, Any]) -> list[str]:
    return [str(part) for part in vm.get("video", {}).get("headless", ["-display", "none"])]


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    """What NT 4 can drive: BIOS, ``pc`` without ACPI, PATA, one CPU, plain VGA, AMD PCnet, no USB."""
    problems: list[str] = []
    if vm.get("firmware", {}).get("type") != "bios":
        problems.append("firmware.type must be bios")
    if str(vm.get("machine")) != "pc":
        problems.append("machine must be pc (Setup drives the PIIX IDE controller)")
    if vm.get("acpi") is not False:
        problems.append("acpi must be false (NT 4 predates ACPI; the install was verified with acpi=off)")
    if vm.get("disk", {}).get("interface") != "ide":
        problems.append("disk.interface must be ide (DOS and NT 4 have no virtio driver)")
    if str(vm.get("disk", {}).get("format", "qcow2")) != "raw":
        problems.append("disk.format must be raw (NT 4 never flushes the disk cache, so qcow2 metadata is lost on a hard kill)")
    if int(vm.get("cpus", 1)) != 1:
        problems.append("cpus must be 1 (an unattended install picks the uniprocessor kernel)")
    if "cirrus" in headless_video_args(vm):
        problems.append('video.headless must not select the Cirrus adapter: the NT 4 (SP6a) Cirrus driver spins the first boot '
                        'forever on QEMU (verified live); the standard VGA ("-vga", "std") reaches the desktop at 640x480')
    if network_adapter(vm) not in {"pcnet", "ne2k_isa"}:
        problems.append("network_device must be pcnet (AMD PCnet, the adapter NT 4 drives on QEMU) or ne2k_isa (installs unattended, "
                        "but the NT 4 NE2000 driver fails to start on QEMU's card: no network); the DEC 21x4 asks for the cable type "
                        "and then hangs the first boot, and NT 4 has no driver for the others")
    if vm.get("usb_tablet"):
        problems.append("usb_tablet must be off (NT 4 has no USB stack; the pointer is PS/2)")
    if problems:
        raise VMError(f"Profile '{vm_name}' cannot run the Windows NT 4.0 unattended install: " + "; ".join(problems))
