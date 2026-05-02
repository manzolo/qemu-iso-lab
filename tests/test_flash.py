import argparse
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.disk_inspect  # noqa: E402
import vmctl.flash  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.state  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class FlashTests(BaseVmctlTestCase):
    def test_cmd_flash_requires_matching_confirmation(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdy", dry_run=True)

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_flash(args)

    def test_cmd_flash_rejects_non_empty_device(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target",
                 side_effect=self.vmctl.VMError("Refusing non-empty target device '/dev/sdz' with signatures: gpt"),
             ):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_flash(args)

    def test_cmd_flash_rejects_efi_image_without_gpt(self):
        self.create_disk()
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target",
                 side_effect=self.vmctl.VMError("EFI guest requires a GPT VM disk before flash; detected: dos"),
             ):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_flash(args)

    def test_cmd_flash_allows_unknown_layout_for_efi_qcow2(self):
        self.create_disk()
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=True, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [{"path": "/dev/sdz1"}],
                         "signatures": [{"type": "gpt"}],
                         "is_root_disk": False,
                         "is_empty": False,
                     },
                     None,
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash(args)

        self.assertEqual(exit_code, 0)
        run_cmd.assert_called_once()

    def test_cmd_flash_dry_run_builds_qemu_img_convert(self):
        disk_path = self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [],
                         "signatures": [],
                         "is_root_disk": False,
                         "is_empty": True,
                     },
                     "dos",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash(args)

        self.assertEqual(exit_code, 0)
        run_cmd.assert_called_once()
        helper_cmd = run_cmd.call_args.args[0]
        self.assertEqual(
            helper_cmd,
            [
                "sudo",
                str((vmctl.state.ROOT / "bin" / "vmctl").resolve()),
                "flash-helper",
                "--vm",
                self.vm_name,
                "--device",
                "/dev/sdz",
                "--confirm-device",
                "/dev/sdz",
            ],
        )

    def test_cmd_flash_force_target_passes_flag_to_helper(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=True, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [{"path": "/dev/sdz1"}],
                         "signatures": [{"type": "gpt"}],
                         "is_root_disk": False,
                         "is_empty": False,
                     },
                     "dos",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash(args)

        self.assertEqual(exit_code, 0)
        helper_cmd = run_cmd.call_args.args[0]
        self.assertIn("--force-target", helper_cmd)

    def test_cmd_flash_helper_runs_convert_on_existing_device(self):
        disk_path = self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False)

        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [],
                         "signatures": [],
                         "is_root_disk": False,
                         "is_empty": True,
                     },
                     "dos",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(vmctl.disk_inspect, "inspect_block_device",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "signatures": [],
                     "is_root_disk": False,
                     "is_empty": True,
                 },
             ), \
             mock.patch.object(vmctl.disk_inspect, "inspect_block_device_basic",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "logical_sector_size": 512,
                     "pttype": "dos",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash_helper(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_cmd.call_count, 3)
        convert_cmd = run_cmd.call_args_list[0].args[0]
        self.assertEqual(
            convert_cmd,
            ["qemu-img", "convert", "-n", "-p", "-f", "qcow2", "-O", "raw", str(disk_path), "/dev/sdz"],
        )

    def test_cmd_flash_helper_relocates_gpt_after_writing_to_larger_disk(self):
        disk_path = self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False)

        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [],
                         "signatures": [],
                         "is_root_disk": False,
                         "is_empty": True,
                     },
                     "gpt",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(vmctl.disk_inspect, "inspect_block_device_basic",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "logical_sector_size": 512,
                     "pttype": "gpt",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash_helper(args)

        self.assertEqual(exit_code, 0)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(
            executed[0],
            ["qemu-img", "convert", "-n", "-p", "-f", "qcow2", "-O", "raw", str(disk_path), "/dev/sdz"],
        )
        self.assertEqual(executed[1], ["blockdev", "--rereadpt", "/dev/sdz"])
        self.assertEqual(executed[2], ["sgdisk", "-e", "/dev/sdz"])
        self.assertEqual(executed[3], ["blockdev", "--rereadpt", "/dev/sdz"])
        self.assertEqual(executed[4], ["sync"])

    def test_cmd_flash_helper_force_target_wipes_signatures_first(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=True)

        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [{"path": "/dev/sdz1"}, {"path": "/dev/sdz2"}],
                         "signatures": [{"type": "gpt"}],
                         "is_root_disk": False,
                         "is_empty": False,
                     },
                     "dos",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(vmctl.disk_inspect, "inspect_block_device_basic",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "logical_sector_size": 512,
                     "pttype": "dos",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_flash_helper(args)

        self.assertEqual(exit_code, 0)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0], ["wipefs", "-a", "-f", "/dev/sdz1"])
        self.assertEqual(executed[1], ["wipefs", "-a", "-f", "/dev/sdz2"])
        self.assertEqual(executed[2], ["wipefs", "-a", "-f", "/dev/sdz"])
        self.assertEqual(executed[3], ["blockdev", "--rereadpt", "/dev/sdz"])

    def test_cmd_flash_helper_continues_when_rereadpt_fails(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=True)

        def fake_run(cmd, dry_run=False, quiet=False):
            if cmd == ["blockdev", "--rereadpt", "/dev/sdz"]:
                raise self.vmctl.subprocess.CalledProcessError(1, cmd)

        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target",
                 return_value=(
                     {
                         "path": "/dev/sdz",
                         "size": 16 * 1024**3,
                         "model": "USB",
                         "mountpoints": [],
                         "children": [{"path": "/dev/sdz1"}],
                         "signatures": [{"type": "gpt"}],
                         "is_root_disk": False,
                         "is_empty": False,
                     },
                     "dos",
                     1 * 1024**3,
                 ),
             ), \
             mock.patch.object(vmctl.disk_inspect, "inspect_block_device_basic",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "logical_sector_size": 512,
                     "pttype": "dos",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run_cmd, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_flash_helper(args)

        self.assertEqual(exit_code, 0)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertIn(["blockdev", "--rereadpt", "/dev/sdz"], executed)
        self.assertIn(["sync"], executed)
        self.assertIn("Kernel did not reread the partition table", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
