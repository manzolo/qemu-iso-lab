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
OPTIONAL_COMMANDS = {"ssh": "guest shell and SSH provisioning", "scp": "copying files during SSH provisioning", "ssh-keygen": "project-generated SSH keys", "dialog": "required only for the TUI fallback", "fzf": "preferred TUI picker", "remote-viewer": "required only by vmctl attach (virt-viewer package)", "virsh": "required only by export-libvirt and the libvirt network lab", "swtpm": "required only by Windows guests exported to libvirt", "sgdisk": "required only by flash/import GPT repair (gdisk package)", "7z": "required only by bootstrap-windows (unpacks the UDF Windows ISO)", "xorriso": "seed ISOs, ISO extraction, prompt-free Windows ISO, pfSense ISO patching", "cloud-localds": "preferred cloud-init seed builder (cloud-image-utils package)", "virtiofsd": "required only by profiles with shared_dir (host folder shared with the guest)", "growisofs": "required only by bootstrap-pfsense (dvd+rw-tools, updates the pfSense ISO in place)"}
OPTIONAL_COMMANDS.update({
    "ddrescue": "allocated-block imports (gddrescue on Debian/Ubuntu, ddrescue on Arch)",
    "ddrescuelog": "verifying complete allocated-block imports (GNU ddrescue package)",
    "partclone.extfs": "allocated-block imports of ext2/3/4 (partclone package)",
    "partclone.ntfs": "allocated-block imports of NTFS (partclone package)",
    "partclone.fat": "allocated-block imports of FAT (partclone package)",
    "partclone.exfat": "allocated-block imports of exFAT (partclone package)",
    "sfdisk": "partition geometry for allocated-block imports (fdisk on Debian/Ubuntu, util-linux on Arch)",
})

COMMON_OVMF_PAIRS = [
    ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
    ("/usr/share/edk2/x64/OVMF_CODE.4m.fd", "/usr/share/edk2/x64/OVMF_VARS.4m.fd"),
    ("/usr/share/edk2/x64/OVMF_CODE.fd", "/usr/share/edk2/x64/OVMF_VARS.fd"),
    ("/usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd", "/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd"),
    ("/usr/share/edk2-ovmf/x64/OVMF_CODE.fd", "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"),
]
