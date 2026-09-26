"""Mutable global state for vmctl.

Every consumer must import the *module* (``from vmctl import state``) and
access attributes at call time (``state.ROOT``).  Never do
``from vmctl.state import ROOT`` — that captures a stale binding and breaks
tests that reassign these values.
"""
from __future__ import annotations

from pathlib import Path

_ORIGINAL_ROOT = Path(__file__).resolve().parent.parent
_ORIGINAL_CONFIG_DIR = _ORIGINAL_ROOT / "vms"

ROOT: Path = _ORIGINAL_ROOT
CONFIG_DIR: Path = _ORIGINAL_CONFIG_DIR
# Not browser-like on purpose: the Fedora archive answers a "Making sure you're not a
# bot!" HTML challenge to anything starting with Mozilla/5.0 and serves the ISO to a plain
# tool identifier (verified against archives.fedoraproject.org). An HTML body saved as an
# ISO is exactly the 4 KB file this repo carried for Fedora Workstation 42.
HTTP_USER_AGENT: str = "vmctl/1.0 (+https://github.com/manzolo/qemu-iso-lab)"

REQUIRED_COMMANDS = ["qemu-system-x86_64", "qemu-img", "python3"]
OPTIONAL_COMMANDS = {
    "ssh": "guest shell and SSH provisioning",
    "scp": "copying files during SSH provisioning",
    "ssh-keygen": "project-generated SSH keys",
    "dialog": "the classic TUI menus without fzf",
    "fzf": "the classic TUI menus",
    "textual": "the vmtui dashboard; without it vmtui opens the fzf/dialog menus",
    "remote-viewer": "vmctl attach",
    "virsh": "export-libvirt and the libvirt network lab",
    "swtpm": "Windows guests exported to libvirt",
    "sgdisk": "GPT repair on flash/import",
    "7z": "bootstrap-windows (unpacks the UDF Windows ISO)",
    "xorriso": "seed ISOs, ISO extraction, Windows/pfSense/Proxmox ISO patching",
    "cloud-localds": "preferred cloud-init seed builder",
    "virtiofsd": "profiles with shared_dir (host folder shared with the guest)",
    "growisofs": "bootstrap-pfsense and bootstrap-freebsd (updates the ISO in place)",
    "ddrescue": "allocated-block imports",
    "ddrescuelog": "verifying complete allocated-block imports",
    "partclone.extfs": "allocated-block imports of ext2/3/4",
    "partclone.ntfs": "allocated-block imports of NTFS",
    "partclone.fat": "allocated-block imports of FAT",
    "partclone.exfat": "allocated-block imports of exFAT",
    "sfdisk": "partition geometry for imports and NTFS growth after flash",
    "ntfsresize": "optional NTFS expansion after flash",
}

COMMON_OVMF_PAIRS = [
    ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
    ("/usr/share/edk2/x64/OVMF_CODE.4m.fd", "/usr/share/edk2/x64/OVMF_VARS.4m.fd"),
    ("/usr/share/edk2/x64/OVMF_CODE.fd", "/usr/share/edk2/x64/OVMF_VARS.fd"),
    ("/usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd", "/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd"),
    ("/usr/share/edk2-ovmf/x64/OVMF_CODE.fd", "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"),
]
