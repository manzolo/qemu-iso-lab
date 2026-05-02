import argparse
import pathlib
import urllib.request
import os
import shutil
import subprocess
import sys
import time
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import io


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl  # noqa: E402
import vmctl.cloud_init  # noqa: E402
import vmctl.config  # noqa: E402
import vmctl.disk_inspect  # noqa: E402
import vmctl.errors  # noqa: E402
import vmctl.flash  # noqa: E402
import vmctl.import_dev  # noqa: E402
import vmctl.iso  # noqa: E402
import vmctl.host_setup  # noqa: E402
import vmctl.lifecycle  # noqa: E402
import vmctl.qemu  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.ssh  # noqa: E402
import vmctl.state  # noqa: E402
import vmctl.ui  # noqa: E402


class _VmctlFacade:
    """Test compatibility shim that flattens the vmctl package surface.

    Forwards attribute reads to the right submodule and ROOT/CONFIG_DIR
    writes to vmctl.state. mock.patch.object should target submodules
    directly (e.g. vmctl.iso, vmctl.runtime) - patching through this
    facade does NOT affect callers in the real submodules.
    """

    _STATE_ATTRS = (
        "ROOT", "CONFIG_DIR", "HTTP_USER_AGENT",
        "REQUIRED_COMMANDS", "OPTIONAL_COMMANDS", "COMMON_OVMF_PAIRS",
    )
    _SEARCH_ORDER = (
        vmctl.lifecycle, vmctl.ssh, vmctl.host_setup, vmctl.flash, vmctl.import_dev, vmctl.disk_inspect,
        vmctl.iso, vmctl.cloud_init, vmctl.qemu, vmctl.config,
        vmctl.runtime, vmctl.ui, vmctl.state, vmctl.errors,
    )

    def __getattr__(self, name):
        if name in self._STATE_ATTRS:
            return getattr(vmctl.state, name)
        for mod in self._SEARCH_ORDER:
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(f"vmctl has no attribute {name!r}")

    def __setattr__(self, name, value):
        if name in self._STATE_ATTRS:
            setattr(vmctl.state, name, value)
            return
        object.__setattr__(self, name, value)


def load_vmctl_module():
    return _VmctlFacade()


class BaseVmctlTestCase(unittest.TestCase):
    def setUp(self):
        self.vmctl = load_vmctl_module()
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.config_dir = self.root / "vms"
        self.original_root = vmctl.state.ROOT
        self.original_config_dir = vmctl.state.CONFIG_DIR
        self.vm_name = "testvm"
        self.vm_config = {
            "name": "Test VM",
            "iso": "isos/test.iso",
            "iso_url": "https://example.invalid/test.iso",
            "disk": {
                "path": "artifacts/testvm/disk.qcow2",
                "size": "1G",
                "format": "qcow2",
                "interface": "virtio",
            },
            "firmware": {"type": "bios"},
            "machine": "pc",
            "memory_mb": 1024,
            "cpus": 1,
            "network": "user",
            "audio": False,
            "video": {"default": "std", "variants": {"std": ["-vga", "std"]}},
        }
        vmctl.state.ROOT = self.root
        vmctl.state.CONFIG_DIR = self.config_dir
        self.write_config_dir()

    def tearDown(self):
        vmctl.state.ROOT = self.original_root
        vmctl.state.CONFIG_DIR = self.original_config_dir
        self.tempdir.cleanup()

    def create_disk(self):
        disk_path = self.root / self.vm_config["disk"]["path"]
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        disk_path.write_text("disk", encoding="utf-8")
        return disk_path

    def write_config_dir(self):
        (self.config_dir / "profiles").mkdir(parents=True, exist_ok=True)
        (self.config_dir / "profiles" / "test.json").write_text(
            json.dumps({"vms": {self.vm_name: self.vm_config}}, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_extra_profile(self, filename: str, payload: dict) -> None:
        (self.config_dir / "profiles" / filename).write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
