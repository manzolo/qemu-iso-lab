"""Host prerequisite helpers: OS detection, install hints, interactive prompt."""
from __future__ import annotations

import sys
from pathlib import Path

from vmctl import runtime


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
        "Optional: dialog/fzf for the TUI, virt-viewer for vmctl attach, virtiofsd for shared_dir profiles.",
        "Optional: cloud-localds/genisoimage/xorriso for seed ISOs, 7z (p7zip) for bootstrap-windows, growisofs (dvd+rw-tools) + python bcrypt for bootstrap-pfsense, swtpm for Windows on libvirt, sgdisk (gdisk) for flash/import GPT repair.",
        "Optional: GNU ddrescue (gddrescue on Debian/Ubuntu), partclone, and sfdisk for allocated-block disk imports.",
    ]


def host_install_commands() -> list[list[str]] | None:
    os_release = read_os_release()
    distro_tokens = {
        token
        for key in ("ID", "ID_LIKE")
        for token in os_release.get(key, "").replace(",", " ").split()
        if token
    }

    if {"arch", "cachyos", "manjaro"} & distro_tokens:
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
    if {"debian", "ubuntu"} & distro_tokens:
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
