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
HTTP_USER_AGENT: str = "Mozilla/5.0 (compatible; vmctl/1.0; +https://local.repo)"

REQUIRED_COMMANDS = ["qemu-system-x86_64", "qemu-img", "python3"]
OPTIONAL_COMMANDS = {"dialog": "required only for the TUI"}
COMMON_OVMF_PAIRS = [
    ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
    ("/usr/share/edk2/x64/OVMF_CODE.4m.fd", "/usr/share/edk2/x64/OVMF_VARS.4m.fd"),
    ("/usr/share/edk2/x64/OVMF_CODE.fd", "/usr/share/edk2/x64/OVMF_VARS.fd"),
    ("/usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd", "/usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd"),
    ("/usr/share/edk2-ovmf/x64/OVMF_CODE.fd", "/usr/share/edk2-ovmf/x64/OVMF_VARS.fd"),
]
