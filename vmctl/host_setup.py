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
        "Install QEMU, Python 3, make, and OVMF/edk2 firmware with your distro package manager.",
        "Optional: install dialog if you want to use make tui.",
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
            "dialog",
            "make",
            "cloud-image-utils",
            "xorriso",
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
                "make",
                "dialog",
                "cloud-image-utils",
                "xorriso",
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
