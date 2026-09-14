"""Windows XP unattended install: ``WINNT.SIF`` render, per-VM bootable ISO (xorriso), CD-ROM args.

Setup reads its answers from ``\\I386\\WINNT.SIF`` on the installation medium: with
``UnattendMode=FullUnattended`` the text stage (partition, format, copy) and the GUI stage both run
without a prompt, and ``[GuiRunOnce]`` runs commands at the first logon, where the completion token
goes to COM1 before the guest shuts itself down.

Two things about the media are not obvious, both verified live on 2026-09-14 with an OEM SP2 ISO.

Some XP images carry no El Torito boot record at all: in that ISO the volume descriptors go from
the primary straight to the terminator, so QEMU has nothing to boot and stops at "Booting from
DVD/CD...". The boot floppy image such a CD was mastered from is not inside the file and cannot be
recovered from it, so the rebuilt ISO gets a small GRUB core image as its El Torito image, and its
``grub.cfg`` chainloads Microsoft's own ``/I386/SETUPLDR.BIN`` with GRUB's ``ntldr`` command. A
medium that already boots keeps its own record, replayed untouched (``-boot_image any replay``).

And the answer file is grafted into the original image with xorriso, never into a rebuilt tree:
``7z`` could not read that ISO completely (one open error) and an image remastered from the
extracted files made Setup stop on "Impossibile copiare il file: cyclad-z.inf".
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from vmctl import runtime, ui
from vmctl.errors import VMError


# Printed by the last [GuiRunOnce] command, at the first logon of the installed system. Unlike
# ReactOS, XP leaves COM1 alone, so a plain `echo > COM1` from the guest reaches the host.
BOOTSTRAP_COMPLETE_TOKEN = "==> Windows XP installation complete!"
# The guest's own `shutdown` after the token is the flush here (CLAUDE.md, token/flush rule).
SHUTDOWN_GRACE_SEC = 180
INSTALL_ISO_NAME = "install.iso"
ANSWER_FILE_PATH = "/I386/WINNT.SIF"
SERVICE_PACK_PATH = "/VMCTL/SP.EXE"
SERVICE_PACK_PATH_WIN = "\\VMCTL\\SP.EXE"
SETUP_SCRIPT_PATH = "/VMCTL/VMCTL.CMD"
SETUP_SCRIPT_PATH_WIN = "\\VMCTL\\VMCTL.CMD"
BOOTSTRAP_FAILED_TOKEN = "==> Windows XP installation FAILED"
GRUB_DIR = "/boot/grub"
GRUB_CORE_PATH = f"{GRUB_DIR}/i386-pc/eltorito.img"
CD_DRIVE_ID = "xpcd0"
# What GRUB needs to read this ISO and hand control to the Microsoft loader.
GRUB_MODULES = ("biosdisk", "iso9660", "ntldr", "part_msdos", "normal", "configfile", "echo")
GRUB_CONFIG = (
    'set timeout=0\n'
    'set default=0\n'
    '\n'
    '# Nothing of GRUB survives this: ntldr hands the machine to Microsoft\'s own CD loader,\n'
    '# which then finds \\I386 and WINNT.SIF on the same volume.\n'
    'menuentry "Windows XP Setup" {\n'
    '    insmod ntldr\n'
    '    ntldr /I386/SETUPLDR.BIN\n'
    '}\n'
)


def _echo(text: str) -> str:
    """A token as `echo` must receive it: cmd reads a bare '>' as a redirection, in a batch file too."""
    return text.replace(">", "^>")


def windowsxp_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    cfg = vm.get("windowsxp_config")
    if cfg is None:
        return None
    if not isinstance(cfg, dict):
        raise VMError("Invalid windowsxp_config: expected object")
    return cfg


def artifact_dir(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "windowsxp"


def install_iso_path(vm: dict[str, Any]) -> Path:
    return artifact_dir(vm) / INSTALL_ISO_NAME


def _sif_value(value: Any) -> str:
    text = str(value)
    if '"' in text or "\n" in text or "\r" in text:
        raise VMError(f"windowsxp_config values cannot contain quotes or newlines: {text!r}")
    return text


def product_key(vm_name: str, vm: dict[str, Any]) -> str:
    """The licence key, which a tracked profile must never carry.

    An OEM medium (``SETUPP.INI`` ending in ``OEM``) asks for a key in the GUI stage, and an
    unattended install that has none stops there with a dialog on a screen nobody is watching.
    """
    cfg = windowsxp_config(vm) or {}
    key = str(cfg.get("product_key") or "").strip()
    if not key:
        raise VMError(
            f"Profile '{vm_name}' has no windowsxp_config.product_key: Windows XP Setup asks for the "
            f"licence key in its GUI stage and the unattended install would stop there. Put your own "
            f"key in vms/profiles/local.json (gitignored), never in a tracked profile."
        )
    return _sif_value(key)


def service_pack_source(vm: dict[str, Any]) -> Path | None:
    """The service pack package to carry on the medium, when the profile asks for one.

    Slipstreaming it would mean running Microsoft's ``update.exe /integrate``, a Win32 binary: the
    package is instead grafted onto the ISO and installed by the first-logon commands, before the
    completion token. Its language must match the medium's.
    """
    cfg = windowsxp_config(vm) or {}
    source = cfg.get("service_pack")
    if not source:
        return None
    path = runtime.resolve_path(str(source))
    if not path.is_file():
        raise VMError(f"windowsxp_config.service_pack does not exist: {ui.pretty_path(path)}")
    return path


def render_setup_script(vm: dict[str, Any]) -> str:
    """The first-logon script carried on the CD, whose whole output goes to COM1.

    Everything that needs quotes, a variable or an exit code lives here rather than in the answer
    file: a ``[GuiRunOnce]`` value cannot contain a double quote (the INI wraps it in one) and
    escaping spaces with ``^`` does not survive ``cmd /c`` parsing - a ``reg query`` of
    "Windows NT\\CurrentVersion" written that way printed nothing at all (verified live).

    Inside a batch file ``>`` is still a redirection, so every token is written ``==^>``.
    """
    cfg = windowsxp_config(vm) or {}
    lines = [
        "@echo off",
        "echo ==^> vmctl: first logon, %DATE% %TIME%",
        "ver",
    ]
    if service_pack_source(vm) is not None:
        lines += [
            "set VMCTLSP=",
            f"for %%d in (D E F G) do if exist %%d:{SERVICE_PACK_PATH_WIN} set VMCTLSP=%%d:{SERVICE_PACK_PATH_WIN}",
            "if not defined VMCTLSP (",
            f"  echo {_echo(BOOTSTRAP_FAILED_TOKEN)}: service pack not found on the CD",
            "  shutdown -s -t 5 -f",
            "  goto :eof",
            ")",
            "echo ==^> vmctl: installing the service pack from %VMCTLSP%",
            # start /wait, because update.exe unpacks itself and hands over to a child: without it
            # the next command would run while the service pack is still working.
            "start /wait %VMCTLSP% /quiet /norestart /nobackup",
            "set VMCTLRC=%ERRORLEVEL%",
            "echo ==^> vmctl: service pack exit code %VMCTLRC%",
            # 3010 is "success, a reboot is pending", which is exactly our case.
            "if not %VMCTLRC%==0 if not %VMCTLRC%==3010 (",
            f"  echo {_echo(BOOTSTRAP_FAILED_TOKEN)}: service pack returned %VMCTLRC%",
            "  shutdown -s -t 5 -f",
            "  goto :eof",
            ")",
        ]
    # AutoLogonCount in the answer file covers the first logon only, the one GuiRunOnce needs: from
    # the second boot XP shows the welcome screen and waits (verified live). A lab guest with no SSH
    # has to reach its desktop by itself, so autologon is made permanent here.
    winlogon = r'"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"'
    user = _sif_value(cfg.get("administrator_name", "Administrator"))
    password = _sif_value(cfg.get("admin_password", "lab"))
    lines += [
        f'reg add {winlogon} /v AutoAdminLogon /t REG_SZ /d 1 /f',
        f'reg add {winlogon} /v DefaultUserName /t REG_SZ /d {user} /f',
        f'reg add {winlogon} /v DefaultPassword /t REG_SZ /d {password} /f',
        # Winlogon decrements AutoLogonCount at every automatic logon and, when it runs out, deletes
        # AutoAdminLogon and DefaultPassword with it - the three values just written. Removing the
        # counter is what makes the autologon permanent (verified live: without this the second boot
        # stops at the welcome screen).
        f'reg delete {winlogon} /v AutoLogonCount /f',
    ]
    if (vm.get("shared_dir") or {}).get("mode") == "vvfat":
        # The share is a plain FAT disk, so there is nothing to mount: say where it is instead of
        # leaving the user to guess which letter Windows gave it.
        lines.append("echo ==^> vmctl: the host share is the extra read-only FAT drive")
    for command in cfg.get("setup_commands", []):
        lines.append(str(command))
    lines += [
        # The evidence the host keeps: what this guest actually is, in the serial log.
        r'reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v CSDVersion',
        f"echo {_echo(BOOTSTRAP_COMPLETE_TOKEN)}",
        "shutdown -s -t 5 -f",
    ]
    return "\r\n".join(lines) + "\r\n"


def first_logon_command() -> str:
    """The single GuiRunOnce entry: run the CD script, everything it prints goes to COM1."""
    return (f"cmd /c for %d in (D E F G) do if exist %d:{SETUP_SCRIPT_PATH_WIN} "
            f"%d:{SETUP_SCRIPT_PATH_WIN} > COM1 2>&1")


def render_winnt_sif(vm_name: str, vm: dict[str, Any]) -> str:
    """The answer file for a fully unattended install ending in the completion token."""
    cfg = windowsxp_config(vm)
    if cfg is None:
        raise VMError(f"Profile '{vm_name}' has no windowsxp_config section")
    # One entry only: the service pack, the checks, the token and the shutdown all live in the
    # script on the CD, which can use quotes and exit codes that an INI value cannot carry.
    run_once = [f'Command0="{first_logon_command()}"']
    lines = [
        "; Rendered by vmctl: Windows XP unattended answer file.",
        "[Data]",
        "AutoPartition=1",
        'MsDosInitiated="0"',
        'UnattendedInstall="Yes"',
        "",
        "[Unattended]",
        "UnattendMode=FullUnattended",
        # Without this, Windows Welcome (msoobe) opens after the GUI stage on "Grazie per aver
        # acquistato Microsoft Windows XP" and waits for a click on Avanti: neither a synthetic key
        # nor the PS/2 relative pointer moves it, so a headless install stops there (verified live
        # 2026-09-14). OemSkipWelcome alone does not cover it; UnattendSwitch skips the whole wizard
        # and, with AutoLogon, the machine goes straight to the desktop where GuiRunOnce runs.
        'UnattendSwitch="Yes"',
        "OemSkipEula=Yes",
        "OemPreinstall=No",
        f"TargetPath=\\{_sif_value(cfg.get('target_path', 'WINDOWS'))}",
        f"FileSystem={_sif_value(cfg.get('file_system', '*'))}",
        "Repartition=Yes",
        "WaitForReboot=No",
        "DriverSigningPolicy=Ignore",
        "",
        "[GuiUnattended]",
        f'AdminPassword="{_sif_value(cfg.get("admin_password", "lab"))}"',
        "EncryptedAdminPassword=No",
        "AutoLogon=Yes",
        "AutoLogonCount=1",
        "OEMSkipRegional=1",
        "OemSkipWelcome=1",
        f"TimeZone={int(cfg.get('timezone', 110))}",
        "",
        "[UserData]",
        f"ProductKey={product_key(vm_name, vm)}",
        f'FullName="{_sif_value(cfg.get("full_name", "Lab User"))}"',
        f'OrgName="{_sif_value(cfg.get("organization", "qemu-iso-lab"))}"',
        f"ComputerName={_sif_value(cfg.get('computer_name', 'WINXP-LAB'))}",
        "",
        "[Identification]",
        f"JoinWorkgroup={_sif_value(cfg.get('workgroup', 'WORKGROUP'))}",
        "",
        "[Networking]",
        "InstallDefaultComponents=Yes",
        "",
        "; Runs at the first logon of the installed system: the profile's commands, then the token",
        "; on COM1 and the guest's own shutdown, which is what the host waits for.",
        "[GuiRunOnce]",
        *run_once,
    ]
    display = cfg.get("display")
    if isinstance(display, dict):
        lines += ["", "[Display]"]
        for key in ("BitsPerPel", "XResolution", "YResolution", "VRefresh"):
            if key in display:
                lines.append(f"{key}={int(display[key])}")
    return "\n".join(lines) + "\n"


def iso_has_boot_record(iso_path: Path) -> bool:
    """Whether the ISO declares an El Torito boot record (volume descriptor type 0)."""
    with iso_path.open("rb") as handle:
        for lba in range(16, 32):
            handle.seek(lba * 2048)
            descriptor = handle.read(7)
            if len(descriptor) < 7 or descriptor[1:6] != b"CD001":
                return False
            if descriptor[0] == 0:
                return True
            if descriptor[0] == 255:  # terminator
                return False
    return False


def build_grub_core(destination: Path, dry_run: bool = False) -> None:
    """The El Torito image for a medium that has none: GRUB, built for CD boot.

    ``-O i386-pc-eltorito`` already prepends ``cdboot.img``; concatenating it again produces an
    image the BIOS loads and cannot run (verified: the guest sits at "Booting from DVD/CD...").
    """
    runtime.require_command("grub-mkimage")
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
    runtime.run(["grub-mkimage", "-O", "i386-pc-eltorito", "-p", GRUB_DIR,
                 "-o", str(destination), *GRUB_MODULES], dry_run=dry_run, quiet=True)


def _stamp(source_iso: Path, answer: str, script: str) -> str:
    st = source_iso.stat()
    digest = hashlib.sha256((answer + script).encode()).hexdigest()
    return f"{source_iso.resolve()}\n{st.st_size}\n{st.st_mtime_ns}\n{digest}\n"


def ensure_install_iso(vm_name: str, vm: dict[str, Any], source_iso: Path, answer: str, dry_run: bool = False) -> Path:
    """The per-VM unattended ISO under ``artifacts/<vm>/windowsxp/``, rebuilt when source or answers change."""
    dest = install_iso_path(vm)
    stamp_path = dest.with_name(dest.name + ".source")
    stamp = _stamp(source_iso, answer, render_setup_script(vm)) if source_iso.is_file() else None
    if dest.is_file() and stamp is not None and stamp_path.is_file() and stamp_path.read_text(encoding="utf-8") == stamp:
        ui.print_status("ok", f"Unattended Windows XP ISO ready: {ui.pretty_path(dest)}")
        return dest
    runtime.require_command("xorriso")
    bootable = iso_has_boot_record(source_iso) if source_iso.is_file() else True
    ui.print_header("Build the unattended Windows XP ISO")
    ui.print_kv("source", ui.pretty_path(source_iso))
    ui.print_kv("target", ui.pretty_path(dest))
    ui.print_kv("boot record", "El Torito, replayed" if bootable else "missing: GRUB chainloads SETUPLDR.BIN")
    work = artifact_dir(vm) / "iso-work"
    if work.exists() and not dry_run:
        shutil.rmtree(work)
    answer_path = work / "WINNT.SIF"
    if not dry_run:
        work.mkdir(parents=True, exist_ok=True)
        answer_path.write_text(answer.replace("\n", "\r\n"), encoding="ascii")
    partial = dest.with_name(dest.name + ".part")
    if not dry_run and partial.exists():
        partial.unlink()
    script_path = work / "VMCTL.CMD"
    if not dry_run:
        script_path.write_text(render_setup_script(vm), encoding="ascii")
    command = ["xorriso", "-indev", str(source_iso), "-outdev", str(partial),
               "-map", str(answer_path), ANSWER_FILE_PATH,
               "-map", str(script_path), SETUP_SCRIPT_PATH]
    service_pack = service_pack_source(vm)
    if service_pack is not None:
        ui.print_kv("service pack", ui.pretty_path(service_pack))
        command += ["-map", str(service_pack), SERVICE_PACK_PATH]
    if bootable:
        command += ["-boot_image", "any", "replay"]
    else:
        core = work / "eltorito.img"
        config = work / "grub.cfg"
        build_grub_core(core, dry_run=dry_run)
        if not dry_run:
            config.write_text(GRUB_CONFIG, encoding="ascii")
        command += [
            "-map", str(core), GRUB_CORE_PATH,
            "-map", str(config), f"{GRUB_DIR}/grub.cfg",
            "-boot_image", "grub", f"bin_path={GRUB_CORE_PATH}",
            "-boot_image", "any", "cat_path=/boot/boot.cat",
            "-boot_image", "any", "emul_type=no_emulation",
            "-boot_image", "any", "boot_info_table=on",
        ]
    command += ["-commit"]
    runtime.run(command, dry_run=dry_run, quiet=True)
    if not dry_run:
        partial.replace(dest)
        shutil.rmtree(work, ignore_errors=True)
        if stamp is not None:
            stamp_path.write_text(stamp, encoding="utf-8")
    ui.print_status("ok", f"Unattended Windows XP ISO: {ui.pretty_path(dest)}")
    return dest


def install_media_args(iso_path: Path) -> list[str]:
    """The installer CD on the second IDE channel, behind the disk in the boot order.

    Setup reboots twice and needs the CD in the GUI stage too, so there is no ``-no-reboot`` and the
    disk, once bootable, wins over the CD exactly like the Windows and ReactOS flows.
    """
    return [
        "-drive", f"id={CD_DRIVE_ID},file={iso_path},format=raw,if=none,media=cdrom,readonly=on",
        "-device", f"ide-cd,drive={CD_DRIVE_ID},bus=ide.1,bootindex=2",
    ]


def headless_video_args(vm: dict[str, Any]) -> list[str]:
    return [str(part) for part in vm.get("video", {}).get("headless", ["-display", "none"])]


def check_profile(vm_name: str, vm: dict[str, Any]) -> None:
    """What Windows XP can boot: BIOS on the ``pc`` machine, a PATA disk, one CPU, no virtio."""
    problems: list[str] = []
    video = headless_video_args(vm)
    if "cirrus" not in video:
        # QEMU's default adapter is the standard VGA, for which XP has no driver: it stays at
        # 640x480 and the shell opens "the screen resolution will be adjusted automatically" at the
        # first logon - a modal dialog, on a headless guest, that nothing dismisses and that holds
        # up GuiRunOnce and therefore the completion token (verified live 2026-09-14). XP ships a
        # Cirrus GD5446 driver, so with that adapter the resolution from [Display] is already in
        # place and no dialog appears.
        problems.append("video.headless must select the Cirrus adapter (\"-vga\", \"cirrus\"): "
                        "with the standard VGA, XP stops at the first-logon display dialog")
    if vm.get("firmware", {}).get("type") != "bios":
        problems.append("firmware.type must be bios (XP has no UEFI loader)")
    if str(vm.get("machine")) != "pc":
        problems.append("machine must be pc (Setup drives the PIIX IDE controller)")
    if vm.get("disk", {}).get("interface") != "ide":
        problems.append("disk.interface must be ide (Setup has no virtio or AHCI storage driver)")
    if int(vm.get("cpus", 1)) != 1:
        problems.append("cpus must be 1 (the uniprocessor HAL is what an unattended install picks)")
    if str(vm.get("network_device", "rtl8139")) not in {"rtl8139", "e1000", "ne2k_pci"}:
        problems.append("network_device must be rtl8139, e1000 or ne2k_pci (XP ships those drivers)")
    if problems:
        raise VMError(f"Profile '{vm_name}' cannot run the Windows XP unattended install: " + "; ".join(problems))
