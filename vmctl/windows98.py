"""Windows 98 unattended install: ``MSBATCH.INF``, a patched boot floppy, a disk prepared by the host.

Nothing of this flow looks like the Windows XP one. Setup runs from real-mode DOS, the CD carries a
1.44 MB El Torito floppy image that boots it, and Setup itself neither partitions nor formats: it
expects a C: that is already there. So three things happen before the guest sees an installer.

**The host prepares the disk.** A partition table with one active FAT32 partition, the filesystem
made by ``mkfs.vfat --offset``, and boot code of our own in sector 0 (``vms/profile-files/windows98/
mbr.asm``): it chainloads the active partition when its boot sector is bootable - which is what the
installed system becomes - and otherwise hands the machine back to the BIOS with ``int 0x18``, so
the boot order moves on to the CD. Without that code the BIOS stops at "Booting from Hard Disk...".
For the same reason the two bytes at offset 0x5A of the fresh FAT32 boot sector, where the jump at
its start lands, become ``int 0x18`` too: dosfstools leaves a stub there that prints "this is not a
bootable disk" and waits for a key. Windows Setup overwrites that sector during the install.

**The boot floppy loses JO.SYS.** Before the DOS on that image runs, a 2 KB program shows the CD's
own menu - "1. Avvio dal disco rigido / 2. Avvio dal CD-ROM" - whose default is the hard disk,
which is why an unattended boot always came back to where it started. That menu is JO.SYS. With the
file gone the image boots DOS directly, into the ``CONFIG.SYS`` menu, whose timeout this flow sets
to zero, and then into an ``AUTOEXEC.BAT`` that mounts the CD and runs Setup with the answer file.
Both files are written with CRLF: DOS does not read a batch file with Unix line endings (it prints
``OFF`` instead of obeying ``@ECHO OFF``).

**The answer file is Microsoft's own format**, and the vendor ships an example of it on the CD
(``\\tools\\SYSREC\\MSBATCH.INF``), which is where the section names here come from. ``[Install]
AddReg`` is what puts the completion script in ``RunOnce``, so the token reaches COM1 at the first
desktop logon and the guest shuts itself down.
"""
from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any

from vmctl import runtime, ui
from vmctl.errors import VMError


BOOTSTRAP_COMPLETE_TOKEN = "==> Windows 98 installation complete!"
BOOTSTRAP_FAILED_TOKEN = "==> Windows 98 installation FAILED"
SHUTDOWN_GRACE_SEC = 180
INSTALL_ISO_NAME = "install.iso"
ANSWER_PATH = "/MSBATCH.INF"
SCRIPT_PATH = "/VMCTL.BAT"
BOOT_IMAGE_PATH = "/BOOT.IMG"
CD_DRIVE_ID = "w98cd0"
# The CD's own boot menu, which defaults to the hard disk and stops an unattended boot.
CD_MENU_FILE = "::JO.SYS"
PARTITION_START_SECTOR = 2048
FAT32_STUB_OFFSET = 0x5A          # where the jump at the start of a FAT32 boot sector lands
BPB_HIDDEN_SECTORS_OFFSET = 0x1C  # sectors before the partition, which its boot code needs
FAT32_BACKUP_SECTOR = 6           # where a FAT32 filesystem keeps its spare boot sector
RETURN_TO_BIOS = bytes([0xCD, 0x18])   # int 0x18
MBR_SOURCE = "vms/profile-files/windows98/mbr.asm"
# Assembled from MBR_SOURCE with: nasm -f bin mbr.asm -o mbr.bin
MBR_CODE = bytes([
    0x31, 0xc0, 0x8e, 0xd0, 0xbc, 0x00, 0x7c, 0x8e, 0xd8, 0x8e, 0xc0, 0xfc, 0xbe, 0x00, 0x7c, 0xbf,
    0x00, 0x06, 0xb9, 0x00, 0x01, 0xf3, 0xa5, 0xea, 0x1c, 0x06, 0x00, 0x00, 0xbd, 0xbe, 0x07, 0xb9,
    0x04, 0x00, 0x80, 0x7e, 0x00, 0x80, 0x74, 0x08, 0x83, 0xc5, 0x10, 0x49, 0x75, 0xf4, 0xeb, 0x20,
    0x66, 0x8b, 0x46, 0x08, 0x66, 0xa3, 0x5c, 0x06, 0xbe, 0x54, 0x06, 0xb4, 0x42, 0xcd, 0x13, 0x72,
    0x0f, 0x81, 0x3e, 0xfe, 0x7d, 0x55, 0xaa, 0x75, 0x07, 0x89, 0xee, 0xea, 0x00, 0x7c, 0x00, 0x00,
    0xcd, 0x18, 0xeb, 0xfc, 0x10, 0x00, 0x01, 0x00, 0x00, 0x7c, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00,
])


def windows98_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("windows98_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid windows98_config: expected object")
    return cfg


def artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "windows98"


def install_iso_path(vm: dict[str, Any]) -> Path:
    return artifact_dir(vm) / INSTALL_ISO_NAME


def _ini_value(value: Any) -> str:
    text = str(value)
    if '"' in text or "\n" in text or "\r" in text:
        raise VMError(f"windows98_config values cannot contain quotes or newlines: {text!r}")
    return text


def _dos_text(lines: list[str]) -> bytes:
    """DOS reads CONFIG.SYS and batch files as CRLF; with Unix endings it obeys none of them."""
    return ("\r\n".join(lines) + "\r\n").encode("cp850")


def boot_image_extent(iso_path: Path) -> tuple[int, int]:
    """(byte offset, size) of the El Torito boot image, read from the ISO's own boot catalog."""
    with iso_path.open("rb") as handle:
        for lba in range(16, 32):
            handle.seek(lba * 2048)
            descriptor = handle.read(2048)
            if len(descriptor) < 2048 or descriptor[1:6] != b"CD001":
                break
            if descriptor[0] == 0:  # boot record
                catalog_lba = struct.unpack("<I", descriptor[0x47:0x4B])[0]
                handle.seek(catalog_lba * 2048)
                catalog = handle.read(2048)
                media = catalog[32 + 1] & 0x0F
                image_lba = struct.unpack("<I", catalog[32 + 8:32 + 12])[0]
                sizes = {1: 1200 * 1024, 2: 1440 * 1024, 3: 2880 * 1024}
                if media not in sizes:
                    raise VMError(f"Unsupported El Torito media type {media} in {ui.pretty_path(iso_path)}: "
                                  f"this flow needs the 1.44 MB floppy image of a Windows 98 CD")
                return image_lba * 2048, sizes[media]
            if descriptor[0] == 255:
                break
    raise VMError(f"No El Torito boot image in {ui.pretty_path(iso_path)}: not a bootable Windows 98 CD")


def render_config_sys(original: str) -> bytes:
    """The CD's own CONFIG.SYS, with its menu answered in advance."""
    lines = []
    seen = False
    for line in original.replace("\r\n", "\n").split("\n"):
        if line.lower().startswith("menudefault="):
            lines.append("menudefault=SETUP_CD,0")
            seen = True
        else:
            lines.append(line)
    if not seen:
        raise VMError("Incompatible boot image: CONFIG.SYS has no menudefault= line (not a Windows 98 boot CD?)")
    while lines and not lines[-1]:
        lines.pop()
    return _dos_text(lines)


def render_autoexec(vm: dict[str, Any]) -> bytes:
    """Find the CD, write the MBR boot code Setup expects, and run Setup with the answer file."""
    cfg = windows98_config(vm) or {}
    switches = " ".join(str(part) for part in cfg.get("setup_switches", ["/IS", "/IE", "/NF"]))
    return _dos_text([
        "@ECHO OFF",
        "set EXPAND=YES",
        "SET DIRCMD=/O:N",
        "set temp=c:\\",
        "set tmp=c:\\",
        "path=a:\\",
        "LH MSCDEX.EXE /D:oemcd001 /L:D",
        "set CDROM=FOO23",
        "FINDCD.EXE",
        'if "%CDROM%"=="FOO23" goto NOCDROM',
        "echo vmctl: installation CD on %CDROM%",
        # No FDISK /MBR here, and that is the whole point: Microsoft's MBR never hands control back
        # to the BIOS, so once it is on the disk the CD keeps its turn at every reboot and Setup
        # starts over from the beginning - which is what happened, with the system already installed
        # and its first-run phase already done (verified live). The MBR written by prepare_disk
        # chainloads the partition as soon as Setup has made it bootable and steps aside until then.
        "path=a:\\;%CDROM%\\",
        "%CDROM%",
        "cd \\WIN98",
        f"SETUP.EXE %CDROM%\\MSBATCH.INF {switches}",
        "goto QUIT",
        ":NOCDROM",
        f"echo {BOOTSTRAP_FAILED_TOKEN.replace('>', '^>')}: no CD-ROM drive found",
        ":QUIT",
    ])


def render_msbatch(vm_name: str, vm: dict[str, Any]) -> str:
    """The answer file, in the shape of the vendor's own example on the CD."""
    cfg = windows98_config(vm)
    if cfg is None:
        raise VMError(f"Profile '{vm_name}' has no windows98_config section")
    lines = [
        "; Rendered by vmctl: Windows 98 unattended answer file.",
        "[BatchSetup]",
        "Version=3.0 (32-bit)",
        "",
        "[Version]",
        'Signature = "$CHICAGO$"',
        "LayoutFile=layout.inf",
        "",
        "[Setup]",
        "Express=1",
        f'InstallDir="{_ini_value(cfg.get("install_dir", "c:\\windows"))}"',
        "InstallType=3",
        "EBD=0",
        "ShowEula=0",
        "ChangeDir=0",
        "OptionalComponents=1",
        # Not 0: the computer name, the workgroup and the description live in [Network], and with
        # the section switched off Setup finds them missing. Installing from MS-DOS it then asks for
        # what is missing - the user information page that stopped every unattended run (the vendor's
        # own sample sets Network=1, and its help says exactly this).
        "Network=1",
        "System=0",
        "CCP=0",
        "CleanBoot=0",
        "Display=0",
        "DevicePath=0",
        "NoDirWarn=1",
        f'TimeZone="{_ini_value(cfg.get("timezone", "W. Europe"))}"',
        "Uninstall=0",
        "NoPrompt2Boot=1",
    ]
    # "ID prodotto - casella in cui digitare il numero di identificazione del prodotto
    # (facoltativo)", says the help of Microsoft's own Batch 98 on this CD: the key is ProductID,
    # and it is optional - it is not what makes Setup stop.
    # With the wrong field Setup falls back to interactive registration despite Display=0.
    product_key = str(cfg.get("product_key") or "").strip()
    if product_key:
        lines.append(f"ProductID={_ini_value(product_key)}")
    lines += [
        "",
        "[System]",
        f'Locale={_ini_value(cfg.get("locale", "L0410"))}',
        f'SelectedKeyboard={_ini_value(cfg.get("keyboard", "KEYBOARD_00000410"))}',
        "",
        "[NameAndOrg]",
        f'Name="{_ini_value(cfg.get("full_name", "Lab User"))}"',
        f'Org="{_ini_value(cfg.get("organization", "qemu-iso-lab"))}"',
        "Display=0",
        "",
        "[Network]",
        f'ComputerName="{_ini_value(cfg.get("computer_name", "WIN98-LAB"))}"',
        f'Workgroup="{_ini_value(cfg.get("workgroup", "WORKGROUP"))}"',
        'Description="qemu-iso-lab"',
        "Display=0",
        "",
        "; RunOnce, not Run: the script says its piece once, at the first desktop logon.",
        "[Install]",
        "AddReg=VmctlRunOnce",
        "",
        "[VmctlRunOnce]",
        f'HKLM,%KEY_RUNONCE%,vmctl,,"command.com /c {_ini_value(cfg.get("script_drive", "D:"))}\\VMCTL.BAT"',
        "",
        "[Strings]",
        'KEY_RUNONCE="SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce"',
    ]
    return "\n".join(lines) + "\n"


def render_setup_script(vm: dict[str, Any]) -> bytes:
    """What runs at the first desktop logon: the profile's commands, the token, the power-off."""
    cfg = windows98_config(vm) or {}
    lines = ["@echo off"]
    lines += [str(command) for command in cfg.get("setup_commands", [])]
    lines += [
        # '>' is a redirection for command.com as well, so the token is echoed escaped.
        f"echo {BOOTSTRAP_COMPLETE_TOKEN.replace('>', '^>')}> COM1",
        "rundll32.exe user.exe,exitwindows",
    ]
    return _dos_text(lines)


def prepare_disk(vm: dict[str, Any], dry_run: bool = False) -> Path:
    """A disk Setup can install on: one active FAT32 partition, and boot code that steps aside.

    Windows 98 Setup does not partition or format, and the DOS tools that would (FDISK, FORMAT) ask
    questions in the language of the medium and demand a reboot in between. The host does it once,
    with no guest involved.
    """
    disk = runtime.resolve_path(vm["disk"]["path"])
    size = str(vm["disk"].get("size", "2G"))
    sectors = _size_in_sectors(size)
    raw = disk.with_suffix(".raw")
    ui.print_kv("disk", f"{ui.pretty_path(disk)} ({size}, FAT32, prepared by the host)")
    if dry_run:
        ui.print_status("ok", "Would prepare the FAT32 disk and its boot code")
        return disk
    runtime.require_command("mkfs.vfat")
    runtime.require_command("qemu-img")
    disk.parent.mkdir(parents=True, exist_ok=True)
    with raw.open("wb") as handle:
        handle.truncate(sectors * 512)
    sector = bytearray(512)
    sector[:len(MBR_CODE)] = MBR_CODE
    entry = bytearray(16)
    entry[0] = 0x80                                   # active
    entry[1:4] = bytes([0xFE, 0xFF, 0xFF])            # CHS is meaningless here; the MBR reads LBA
    entry[4] = 0x0C                                   # FAT32 LBA
    entry[5:8] = bytes([0xFE, 0xFF, 0xFF])
    entry[8:12] = struct.pack("<I", PARTITION_START_SECTOR)
    entry[12:16] = struct.pack("<I", sectors - PARTITION_START_SECTOR)
    sector[446:462] = entry
    sector[510:512] = b"\x55\xaa"
    with raw.open("r+b") as handle:
        handle.write(bytes(sector))
    runtime.run(["mkfs.vfat", "-F", "32", "--offset", str(PARTITION_START_SECTOR),
                 "-n", "WIN98", str(raw), str((sectors - PARTITION_START_SECTOR) // 2)],
                dry_run=False, quiet=True)
    with raw.open("r+b") as handle:
        handle.seek(PARTITION_START_SECTOR * 512 + FAT32_STUB_OFFSET)
        handle.write(RETURN_TO_BIOS)
        # mkfs.vfat --offset writes the filesystem where it is told but leaves BPB_HiddSec at zero.
        # Windows Setup keeps that field when it writes its own boot code over it, and its loader
        # then looks for IO.SYS at the wrong absolute sector: the guest hangs at "Booting from Hard
        # Disk..." after the file-copy stage (verified live). The backup sector carries it too.
        for lba in (PARTITION_START_SECTOR, PARTITION_START_SECTOR + FAT32_BACKUP_SECTOR):
            handle.seek(lba * 512 + BPB_HIDDEN_SECTORS_OFFSET)
            handle.write(struct.pack("<I", PARTITION_START_SECTOR))
    if disk.exists():
        disk.unlink()
    runtime.run(["qemu-img", "convert", "-O", "qcow2", str(raw), str(disk)], dry_run=False, quiet=True)
    raw.unlink()
    ui.print_status("ok", f"Disk ready for Setup: {ui.pretty_path(disk)}")
    return disk


def _size_in_sectors(size: str) -> int:
    text = size.strip().upper()
    multipliers = {"G": 1024 ** 3, "M": 1024 ** 2, "K": 1024}
    factor = multipliers.get(text[-1:], 1)
    number = text[:-1] if text[-1:] in multipliers else text
    try:
        total = int(float(number) * factor)
    except ValueError as exc:
        raise VMError(f"Invalid disk.size for a Windows 98 profile: {size!r}") from exc
    if total < 512 * 1024 * 1024:
        raise VMError(f"disk.size {size!r} is too small for Windows 98 (512M is the floor)")
    return total // 512


def patch_boot_image(source_iso: Path, work: Path, vm: dict[str, Any], dry_run: bool = False) -> Path:
    """The CD's boot floppy without its menu, and with startup files that install by themselves."""
    target = work / "BOOT.IMG"
    if dry_run:
        return target
    runtime.require_command("mcopy")
    offset, size = boot_image_extent(source_iso)
    with source_iso.open("rb") as handle:
        handle.seek(offset)
        target.write_bytes(handle.read(size))
    config_original = subprocess.run(["mtype", "-i", str(target), "::CONFIG.SYS"],
                                     capture_output=True, text=True, check=False)
    if config_original.returncode != 0:
        raise VMError(f"Incompatible boot image in {ui.pretty_path(source_iso)}: no CONFIG.SYS on it")
    (work / "CONFIG.SYS").write_bytes(render_config_sys(config_original.stdout))
    (work / "AUTOEXEC.BAT").write_bytes(render_autoexec(vm))
    # The CD boot menu lives in this file and defaults to the hard disk; without it DOS starts.
    # A medium that has no such menu is fine: mdel failing means there was nothing to remove.
    ui.print_command(["mdel", "-i", str(target), CD_MENU_FILE])
    subprocess.run(["mdel", "-i", str(target), CD_MENU_FILE], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name in ("CONFIG.SYS", "AUTOEXEC.BAT"):
        runtime.run(["mcopy", "-o", "-i", str(target), str(work / name), f"::{name}"],
                    dry_run=False, quiet=True)
    return target


def _stamp(source_iso: Path, answer: str, autoexec: bytes, script: bytes) -> str:
    st = source_iso.stat()
    digest = hashlib.sha256(answer.encode() + autoexec + script).hexdigest()
    return f"{source_iso.resolve()}\n{st.st_size}\n{st.st_mtime_ns}\n{digest}\n"


def ensure_install_iso(vm_name: str, vm: dict[str, Any], source_iso: Path, answer: str, dry_run: bool = False) -> Path:
    """The per-VM install CD: answer file, first-logon script, and the patched boot floppy."""
    dest = install_iso_path(vm)
    stamp_path = dest.with_name(dest.name + ".source")
    script = render_setup_script(vm)
    stamp = (_stamp(source_iso, answer, render_autoexec(vm), script) if source_iso.is_file() else None)
    if dest.is_file() and stamp is not None and stamp_path.is_file() and stamp_path.read_text(encoding="utf-8") == stamp:
        ui.print_status("ok", f"Unattended Windows 98 ISO ready: {ui.pretty_path(dest)}")
        return dest
    runtime.require_command("xorriso")
    ui.print_header("Build the unattended Windows 98 ISO")
    ui.print_kv("source", ui.pretty_path(source_iso))
    ui.print_kv("target", ui.pretty_path(dest))
    work = artifact_dir(vm) / "iso-work"
    if work.exists() and not dry_run:
        shutil.rmtree(work)
    if not dry_run:
        work.mkdir(parents=True, exist_ok=True)
    boot_image = patch_boot_image(source_iso, work, vm, dry_run=dry_run)
    answer_path, script_path = work / "MSBATCH.INF", work / "VMCTL.BAT"
    if not dry_run:
        answer_path.write_text(answer.replace("\n", "\r\n"), encoding="cp850")
        script_path.write_bytes(script)
    # xorriso refuses to overwrite an existing -outdev and leaves the old image in place, which is
    # indistinguishable from a successful build until the guest boots the wrong CD (verified).
    partial = dest.with_name(dest.name + ".part")
    if not dry_run and partial.exists():
        partial.unlink()
    runtime.run(["xorriso", "-indev", str(source_iso), "-outdev", str(partial),
                 # No untranslated_names here, unlike the NT-family flow: this CD's own names come
                 # back from xorriso as they are written on it, and keeping them verbatim left the
                 # DOS on the boot floppy unable to find \WIN98 (verified live). The NT installers
                 # need the opposite, because their media carry names with a tilde.
                 "-map", str(answer_path), ANSWER_PATH,
                 "-map", str(script_path), SCRIPT_PATH,
                 "-map", str(boot_image), BOOT_IMAGE_PATH,
                 "-boot_image", "any", f"bin_path={BOOT_IMAGE_PATH}",
                 "-boot_image", "any", "emul_type=diskette",
                 "-commit"], dry_run=dry_run, quiet=True)
    if not dry_run:
        partial.replace(dest)
        shutil.rmtree(work, ignore_errors=True)
        if stamp is not None:
            stamp_path.write_text(stamp, encoding="utf-8")
    ui.print_status("ok", f"Unattended Windows 98 ISO: {ui.pretty_path(dest)}")
    return dest


def install_media_args(iso_path: Path) -> list[str]:
    """The installer CD behind the disk: our MBR steps aside until Setup has made C: bootable."""
    return [
        "-drive", f"id={CD_DRIVE_ID},file={iso_path},format=raw,if=none,media=cdrom,readonly=on",
        "-device", f"ide-cd,drive={CD_DRIVE_ID},bus=ide.1,bootindex=2",
    ]


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    """What Windows 98 can drive: BIOS, the ``pc`` machine, a PATA disk, one CPU, no xHCI."""
    problems: list[str] = []
    if vm.get("firmware", {}).get("type") != "bios":
        problems.append("firmware.type must be bios")
    if str(vm.get("machine")) != "pc":
        problems.append("machine must be pc (Setup drives the PIIX IDE controller)")
    if vm.get("disk", {}).get("interface") != "ide":
        problems.append("disk.interface must be ide (real-mode DOS has no virtio driver)")
    if int(vm.get("cpus", 1)) != 1:
        problems.append("cpus must be 1 (Windows 98 is uniprocessor)")
    if int(vm.get("memory_mb", 0)) > 512:
        problems.append("memory_mb must be 512 or less (more memory makes Windows 98 fail to start)")
    if str(vm.get("usb_controller", "qemu-xhci")) != "builtin" and vm.get("usb_tablet"):
        problems.append('usb_controller must be "builtin" when usb_tablet is on (no xHCI driver)')
    if problems:
        raise VMError(f"Profile '{vm_name}' cannot run the Windows 98 unattended install: " + "; ".join(problems))
