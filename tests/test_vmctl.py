import argparse
import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "bin" / "vmctl"


def load_vmctl_module():
    loader = importlib.machinery.SourceFileLoader("vmctl_module", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class VmctlTests(unittest.TestCase):
    def setUp(self):
        self.vmctl = load_vmctl_module()
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.config_path = self.root / "vms.json"
        self.original_root = self.vmctl.ROOT
        self.original_config_path = self.vmctl.CONFIG_PATH
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
        self.vmctl.ROOT = self.root
        self.vmctl.CONFIG_PATH = self.config_path
        self.write_config()

    def tearDown(self):
        self.vmctl.ROOT = self.original_root
        self.vmctl.CONFIG_PATH = self.original_config_path
        self.tempdir.cleanup()

    def write_config(self):
        self.config_path.write_text(
            (
                "{\n"
                '  "vms": {\n'
                f'    "{self.vm_name}": '
                + json.dumps(self.vm_config, indent=6)
                + "\n"
                "  }\n"
                "}\n"
            ),
            encoding="utf-8",
        )

    def create_disk(self):
        disk_path = self.root / self.vm_config["disk"]["path"]
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        disk_path.write_text("disk", encoding="utf-8")
        return disk_path

    def test_ensure_iso_skips_download_when_file_exists(self):
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("already here", encoding="utf-8")

        with mock.patch.object(self.vmctl, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        download_file.assert_not_called()

    def test_cmd_prep_downloads_iso_when_missing(self):
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_prep(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once()
        run_cmd.assert_called_once()
        qemu_img_cmd = run_cmd.call_args.args[0]
        self.assertEqual(qemu_img_cmd[:3], ["qemu-img", "create", "-f"])

    def test_cmd_prep_fails_without_iso_url(self):
        self.vm_config.pop("iso_url")
        self.write_config()
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(self.vmctl, "require_command"):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_prep(args)

    def test_cmd_install_dry_run_builds_qemu_command_with_cdrom(self):
        disk_path = self.create_disk()
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, video="std", dry_run=True)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True)
        run_cmd.assert_called_once()
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertEqual(run_cmd.call_args.kwargs["dry_run"], True)
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn(f"file={disk_path},format=qcow2,if=virtio", qemu_cmd)
        self.assertIn("-cdrom", qemu_cmd)
        self.assertEqual(qemu_cmd[qemu_cmd.index("-cdrom") + 1], str(iso_path))
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)

    def test_cmd_start_dry_run_builds_qemu_command_without_cdrom(self):
        disk_path = self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, dry_run=True)

        with mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run") as run_cmd:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        run_cmd.assert_called_once()
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertEqual(run_cmd.call_args.kwargs["dry_run"], True)
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn(f"file={disk_path},format=qcow2,if=virtio", qemu_cmd)
        self.assertNotIn("-cdrom", qemu_cmd)
        self.assertIn("-netdev", qemu_cmd)
        self.assertIn("user,id=n1", qemu_cmd)

    def test_common_args_tcg_headless_serial(self):
        disk_path = self.create_disk()

        with mock.patch.object(self.vmctl, "require_command"):
            qemu_cmd = self.vmctl.common_args(
                self.vm_config,
                variant=None,
                dry_run=True,
                accel="tcg",
                headless=True,
                serial_stdio=True,
                no_reboot=True,
            )

        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertNotIn("-enable-kvm", qemu_cmd)
        self.assertIn("pc,accel=tcg", qemu_cmd)
        self.assertIn(f"file={disk_path},format=qcow2,if=virtio", qemu_cmd)
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertIn("-serial", qemu_cmd)
        self.assertIn("-no-reboot", qemu_cmd)

    def test_cmd_boot_check_dry_run_uses_ci_settings(self):
        self.create_disk()
        self.vm_config["ci"] = {
            "accel": "tcg",
            "headless": True,
            "boot_from": "cdrom",
            "auto_input": [{"match": "boot:", "send": "\n"}],
            "expect": "login:",
            "timeout_sec": 42,
        }
        self.write_config()
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, expect=None, timeout=None, dry_run=True)

        with mock.patch.object(self.vmctl, "download_file") as download_file, \
             mock.patch.object(self.vmctl, "require_command"), \
             mock.patch.object(self.vmctl, "run_and_expect") as run_and_expect:
            exit_code = self.vmctl.cmd_boot_check(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True)
        run_and_expect.assert_called_once()
        qemu_cmd, expected_text, timeout_sec = run_and_expect.call_args.args
        self.assertIn("-cdrom", qemu_cmd)
        self.assertEqual(qemu_cmd[qemu_cmd.index("-cdrom") + 1], str(iso_path))
        self.assertEqual(expected_text, "login:")
        self.assertEqual(timeout_sec, 42)
        self.assertEqual(
            run_and_expect.call_args.kwargs["auto_inputs"],
            [("boot:", "\n")],
        )


if __name__ == "__main__":
    unittest.main()
