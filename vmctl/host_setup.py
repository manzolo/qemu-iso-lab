"""Host prerequisite helpers: OS detection, install hints, `vmctl setup --install`, interactive prompt."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from vmctl import qemu, runtime, state, ui
from vmctl.errors import VMError

# What `vmctl setup --install NAME` installs: every name `vmctl setup` checks, plus the firmware,
# mapped to the package that brings it on each package manager. Textual is not a distro package:
# it goes into the repository's .venv-tui, where bin/vmtui looks for it first.
TOOL_PACKAGES: dict[str, dict[str, str]] = {
    "qemu-system-x86_64": {"apt": "qemu-system-x86", "pacman": "qemu-desktop"},
    "qemu-img": {"apt": "qemu-utils", "pacman": "qemu-img"},
    "python3": {"apt": "python3", "pacman": "python"},
    "ovmf": {"apt": "ovmf", "pacman": "edk2-ovmf"},
    "ssh": {"apt": "openssh-client", "pacman": "openssh"},
    "scp": {"apt": "openssh-client", "pacman": "openssh"},
    "ssh-keygen": {"apt": "openssh-client", "pacman": "openssh"},
    "dialog": {"apt": "dialog", "pacman": "dialog"},
    "fzf": {"apt": "fzf", "pacman": "fzf"},
    "remote-viewer": {"apt": "virt-viewer", "pacman": "virt-viewer"},
    "virsh": {"apt": "libvirt-clients", "pacman": "libvirt"},
    "swtpm": {"apt": "swtpm", "pacman": "swtpm"},
    "sgdisk": {"apt": "gdisk", "pacman": "gdisk"},
    "7z": {"apt": "p7zip-full", "pacman": "p7zip"},
    "xorriso": {"apt": "xorriso", "pacman": "xorriso"},
    "cloud-localds": {"apt": "cloud-image-utils", "pacman": "cloud-image-utils"},
    "virtiofsd": {"apt": "virtiofsd", "pacman": "virtiofsd"},
    "growisofs": {"apt": "dvd+rw-tools", "pacman": "dvd+rw-tools"},
    "ddrescue": {"apt": "gddrescue", "pacman": "ddrescue"},
    "ddrescuelog": {"apt": "gddrescue", "pacman": "ddrescue"},
    "partclone.extfs": {"apt": "partclone", "pacman": "partclone"},
    "partclone.ntfs": {"apt": "partclone", "pacman": "partclone"},
    "partclone.fat": {"apt": "partclone", "pacman": "partclone"},
    "partclone.exfat": {"apt": "partclone", "pacman": "partclone"},
    "sfdisk": {"apt": "fdisk", "pacman": "util-linux"},
    "ntfsresize": {"apt": "ntfs-3g", "pacman": "ntfs-3g"},
}
TEXTUAL = "textual"


# How `vmctl setup` groups what it checks: one line per group when everything is there, and one
# line per missing tool (purpose + package) underneath. Every name of REQUIRED_COMMANDS and
# OPTIONAL_COMMANDS belongs to exactly one group (a test fails otherwise).
SETUP_GROUPS: list[tuple[str, list[str]]] = [
    ("Required", ["qemu-system-x86_64", "qemu-img", "python3"]),
    ("Dashboard", ["textual", "fzf", "dialog"]),
    ("SSH", ["ssh", "scp", "ssh-keygen"]),
    ("Installers", ["xorriso", "cloud-localds", "7z", "growisofs", "virtiofsd"]),
    ("Viewer, libvirt", ["remote-viewer", "virsh", "swtpm"]),
    ("Disk import, flash", ["sgdisk", "sfdisk", "ntfsresize", "ddrescue", "ddrescuelog",
                            "partclone.extfs", "partclone.ntfs", "partclone.fat", "partclone.exfat"]),
]


def compact_names(names: list[str]) -> str:
    """``a · b · partclone.{extfs,ntfs}``: one family of tools shares its prefix."""
    parts: list[str] = []
    families: dict[str, list[str]] = {}
    for name in names:
        prefix, dot, suffix = name.partition(".")
        if dot:
            if prefix not in families:
                families[prefix] = []
                parts.append(prefix + ".")
            families[prefix].append(suffix)
        else:
            parts.append(name)
    return " · ".join(
        f"{part}{{{','.join(families[part[:-1]])}}}" if part.endswith(".") and len(families[part[:-1]]) > 1
        else f"{part}{families[part[:-1]][0]}" if part.endswith(".") else part
        for part in parts)


def tool_package(name: str) -> str:
    if name == TEXTUAL:
        return ".venv-tui, no sudo"
    return TOOL_PACKAGES[name][package_manager() or "apt"] + " package"


def textual_location() -> str:
    python = textual_python()
    return ".venv-tui" if python == str(textual_venv() / "bin/python") else str(python)


def kvm_status() -> tuple[bool, str]:
    """/dev/kvm usable by this user: without it every guest runs under TCG, many times slower."""
    device = Path("/dev/kvm")
    if not device.exists():
        return False, "/dev/kvm missing: guests run without acceleration (enable virtualization in the firmware, load kvm_intel/kvm_amd)"
    if not os.access(device, os.R_OK | os.W_OK):
        return False, "/dev/kvm is not writable by this user: add yourself to the kvm group and log in again"
    return True, "/dev/kvm"


def read_os_release() -> dict[str, str]:
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        return {}

    data: dict[str, str] = {}
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


def host_install_hints() -> list[str]:
    commands = host_install_commands()
    if commands is not None:
        return [runtime.shell_join(cmd) for cmd in commands]
    return [
        "Install QEMU, Python 3, make, OVMF/edk2 firmware, OpenSSH client tools, and libvirt/virsh with your distro package manager.",
        "Optional: Textual for the vmtui dashboard (make install textual), dialog/fzf for the classic TUI menus, virt-viewer for vmctl attach, virtiofsd for shared_dir profiles.",
        "Optional: cloud-localds/genisoimage/xorriso for seed ISOs, 7z (p7zip) for bootstrap-windows, growisofs (dvd+rw-tools) + python bcrypt for bootstrap-pfsense, swtpm for Windows on libvirt, sgdisk (gdisk) for flash/import GPT repair.",
        "Optional: GNU ddrescue (gddrescue on Debian/Ubuntu), partclone, and sfdisk for allocated-block disk imports.",
    ]


def package_manager() -> str | None:
    """'apt' or 'pacman' from /etc/os-release, None on a distribution this file has no packages for."""
    os_release = read_os_release()
    distro_tokens = {
        token
        for key in ("ID", "ID_LIKE")
        for token in os_release.get(key, "").replace(",", " ").split()
        if token
    }
    if {"arch", "cachyos", "manjaro"} & distro_tokens:
        return "pacman"
    if {"debian", "ubuntu"} & distro_tokens:
        return "apt"
    return None


def host_install_commands() -> list[list[str]] | None:
    manager = package_manager()
    if manager == "pacman":
        return [[
            "sudo",
            "pacman",
            "-S",
            "qemu-desktop",
            "qemu-base",
            "edk2-ovmf",
            "python",
            "openssh",
            "libvirt",
            "dialog",
            "make",
            "fzf",
            "cloud-image-utils",
            "xorriso",
            "virtiofsd",
            "virt-viewer",
            "p7zip",
            "dvd+rw-tools",
            "python-bcrypt",
            "swtpm",
            "gdisk",
            "ddrescue",
            "partclone",
            "util-linux",
        ]]
    if manager == "apt":
        return [
            ["sudo", "apt", "update"],
            [
                "sudo",
                "apt",
                "install",
                "-y",
                "qemu-system-x86",
                "qemu-utils",
                "ovmf",
                "python3",
                "openssh-client",
                "libvirt-clients",
                "libvirt-daemon-system",
                "make",
                "dialog",
                "fzf",
                "cloud-image-utils",
                "xorriso",
                "virtiofsd",
                "virt-viewer",
                "p7zip-full",
                "dvd+rw-tools",
                "python3-bcrypt",
                "swtpm",
                "gdisk",
                "gddrescue",
                "partclone",
                "fdisk",
            ],
        ]
    return None


def textual_python() -> str | None:
    """The interpreter bin/vmtui would open the dashboard with, same candidates in the same order:
    $VMTUI_TEXTUAL_PYTHON alone when set, else the repo's .venv-tui, then python3."""
    override = os.environ.get("VMTUI_TEXTUAL_PYTHON")
    candidates = [override] if override else [str(textual_venv() / "bin/python"), "python3"]
    for candidate in candidates:
        try:
            probe = subprocess.run([candidate, "-c", "import textual"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def textual_venv() -> Path:
    return state.ROOT / ".venv-tui"


def tool_present(name: str) -> bool:
    """Same lookup the flows use: virtiofsd lives in /usr/libexec, 7z may be 7zz/7za, Textual is
    a Python module that bin/vmtui looks for in .venv-tui first, the firmware is a file pair."""
    if name == TEXTUAL:
        return textual_python() is not None
    if name == "ovmf":
        return any(Path(code).exists() for code, _ in state.COMMON_OVMF_PAIRS)
    if name == "virtiofsd":
        return qemu.find_virtiofsd() is not None
    if name == "7z":
        return any(shutil.which(candidate) for candidate in ("7z", "7zz", "7za"))
    return shutil.which(name) is not None


def installable_names() -> list[str]:
    return [*TOOL_PACKAGES, TEXTUAL]


def missing_tools() -> list[str]:
    return [name for name in installable_names() if not tool_present(name)]


def apt_available(packages: list[str]) -> set[str] | None:
    """The packages apt can install on this release (``apt-cache policy`` shows a candidate), or
    None when that cannot be asked. Ubuntu 22.04 has no ``virtiofsd`` package (the daemon ships in
    qemu-system-common), and one unknown name makes ``apt install`` refuse the whole list."""
    if not packages or shutil.which("apt-cache") is None:
        return None
    try:
        result = subprocess.run(["apt-cache", "policy", *packages], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    found: set[str] = set()
    current = None
    for line in result.stdout.splitlines():
        if line and not line[0].isspace() and line.endswith(":"):
            current = line[:-1]
        elif current and line.strip().startswith("Candidate:") and line.split(":", 1)[1].strip() != "(none)":
            found.add(current)
    return found


def package_install_commands(names: list[str], manager: str, extra: list[str] | None = None) -> list[list[str]]:
    packages = list(dict.fromkeys([*(TOOL_PACKAGES[name][manager] for name in names), *(extra or [])]))
    if manager == "apt":
        available = apt_available(packages)
        if available is not None:
            for package in [package for package in packages if package not in available]:
                ui.print_status("warn", f"{package}: no apt package on this release, skipped", ok=False)
            packages = [package for package in packages if package in available]
    if not packages:
        return []
    if manager == "pacman":
        return [["sudo", "pacman", "-S", "--needed", *packages]]
    return [["sudo", "apt", "update"], ["sudo", "apt", "install", "-y", *packages]]


def textual_install_commands() -> list[list[str]]:
    venv = textual_venv()
    commands = [] if (venv / "bin/python").exists() else [["python3", "-m", "venv", str(venv)]]
    return [*commands, [str(venv / "bin/python"), "-m", "pip", "install", "--quiet", "-e", f"{state.ROOT}[tui]"]]


def install_tools(names: list[str], *, assume_yes: bool = False, dry_run: bool = False) -> None:
    """`vmctl setup --install [NAME...]`: the named tools, or every missing one when none is named;
    a name already present is reported and skipped. Asks before running anything unless --yes."""
    unknown = [name for name in names if name not in installable_names()]
    if unknown:
        raise VMError(f"Unknown tool(s): {', '.join(unknown)}. Installable: {', '.join(installable_names())}")
    if names:
        for name in names:
            if tool_present(name):
                ui.print_status("ok", f"{name} is already installed")
        wanted = [name for name in dict.fromkeys(names) if not tool_present(name)]
    else:
        wanted = missing_tools()
    if not wanted:
        ui.print_note("Nothing to install.")
        return

    system = [name for name in wanted if name != TEXTUAL]
    manager = package_manager()
    if system and manager is None:
        raise VMError(f"No package list for this distribution; install {', '.join(system)} with its package manager "
                      f"({' '.join(host_install_hints())})")
    # Debian and Ubuntu split ensurepip out of python3: `python3 -m venv` fails without python3-venv.
    extra = ["python3-venv"] if TEXTUAL in wanted and manager == "apt" and not (textual_venv() / "bin/python").exists() else []
    commands = package_install_commands(system, manager, extra) if (system or extra) and manager else []
    if TEXTUAL in wanted:
        commands += textual_install_commands()

    for name in wanted:
        source = ".venv-tui (pip install -e \".[tui]\")" if name == TEXTUAL else TOOL_PACKAGES[name][manager or "apt"]
        ui.print_note(f"{name} <- {source}")
    for cmd in commands:
        ui.print_note(f"  {runtime.shell_join(cmd)}")
    if not (assume_yes or dry_run) and not runtime.confirm_default_no("Run these commands?"):
        raise VMError("Not confirmed (pass --yes to skip the question in scripts)")
    for cmd in commands:
        try:
            runtime.run(cmd, dry_run=dry_run)
        except subprocess.CalledProcessError as exc:
            hint = " (on Debian/Ubuntu `python3 -m venv` needs the python3-venv package)" if cmd[1:3] == ["-m", "venv"] else ""
            raise VMError(f"Installation failed: {runtime.shell_join(cmd)}{hint}") from exc


def prompt_yes_no(prompt: str) -> bool:
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return False
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def prompt_yes_no_default_yes(prompt: str) -> bool:
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return True
    try:
        answer = input(f"{prompt} [Y/n] ").strip().lower()
    except EOFError:
        return True
    if not answer:
        return True
    return answer in {"y", "yes"}
