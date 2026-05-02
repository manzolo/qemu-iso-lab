import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.disk_inspect  # noqa: E402
import vmctl.flash  # noqa: E402
import vmctl.import_dev  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.state  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class ImportDevTests(BaseVmctlTestCase):
    def test_cmd_import_device_requires_matching_confirmation(self):
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdy", dry_run=True)

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_import_device(args)

    def test_cmd_import_device_dry_run_builds_helper_command(self):
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.import_dev, "validate_import_source",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [],
                     "logical_sector_size": 512,
                     "pttype": None,
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_import_device(args)

        self.assertEqual(exit_code, 0)
        helper_cmd = run_cmd.call_args.args[0]
        self.assertEqual(
            helper_cmd,
            [
                "sudo",
                str((vmctl.state.ROOT / "bin" / "vmctl").resolve()),
                "import-helper",
                "--vm",
                self.vm_name,
                "--device",
                "/dev/sdz",
                "--confirm-device",
                "/dev/sdz",
            ],
        )

    def test_cmd_import_helper_runs_bounded_pipeline_for_dos_disk(self):
        disk_path = self.root / self.vm_config["disk"]["path"]
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz")

        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.import_dev, "validate_import_source",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 16 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [{"type": "part", "start": 2048, "size": 4 * 1024**3}],
                     "logical_sector_size": 512,
                     "pttype": "dos",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd, \
             mock.patch.object(vmctl.runtime, "run_progress") as run_progress, \
             mock.patch.object(vmctl.runtime, "run_pipeline") as run_pipeline, \
             mock.patch.object(vmctl.flash, "maybe_restore_sudo_owner") as restore_owner, \
             mock.patch.object(vmctl.flash, "maybe_restore_sudo_owner_tree") as restore_tree:
            exit_code = self.vmctl.cmd_import_helper(args)

        self.assertEqual(exit_code, 0)
        run_cmd.assert_not_called()
        run_progress.assert_not_called()
        run_pipeline.assert_called_once_with(
            [
                [
                    "dd",
                    "if=/dev/sdz",
                    "iflag=fullblock,count_bytes",
                    f"count={self.vmctl.round_up((2048 * 512) + (4 * 1024**3), 1024**2)}",
                    "bs=4M",
                    "status=progress",
                ],
                ["qemu-img", "convert", "-p", "-f", "raw", "-O", "qcow2", "-", str(disk_path)],
            ],
            dry_run=False,
        )
        restore_owner.assert_called_once_with(disk_path)
        restore_tree.assert_called_once_with(disk_path.parent)

    def test_cmd_import_helper_compacts_gpt_and_repairs_backup_header(self):
        disk_path = self.root / self.vm_config["disk"]["path"]
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz")
        temp_root = self.root / "tmp-import"

        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.import_dev, "validate_import_source",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 115 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [{"type": "part", "start": 2048, "size": 32 * 1024**3}],
                     "logical_sector_size": 512,
                     "pttype": "gpt",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(vmctl.disk_inspect, "maybe_read_gpt_geometry", return_value={}), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd, \
             mock.patch.object(vmctl.runtime, "run_progress") as run_progress, \
             mock.patch.object(tempfile, "mkdtemp", return_value=str(temp_root)), \
             mock.patch.object(vmctl.runtime, "run_pipeline") as run_pipeline, \
             mock.patch.object(vmctl.flash, "maybe_restore_sudo_owner") as restore_owner, \
             mock.patch.object(vmctl.flash, "maybe_restore_sudo_owner_tree") as restore_tree:
            exit_code = self.vmctl.cmd_import_helper(args)

        self.assertEqual(exit_code, 0)
        expected_count = self.vmctl.round_up((2048 * 512) + (32 * 1024**3) + (33 * 512), 1024**2)
        temp_raw = temp_root / "source.raw"
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(
            executed[0],
            [
                "dd",
                "if=/dev/sdz",
                f"of={temp_raw}",
                "iflag=fullblock,count_bytes",
                f"count={expected_count}",
                "bs=4M",
                "conv=sparse",
                "status=progress",
            ],
        )
        self.assertEqual(executed[1], ["sgdisk", "-e", str(temp_raw)])
        self.assertEqual(executed[2], ["sgdisk", "-v", str(temp_raw)])
        run_progress.assert_called_once_with(
            ["qemu-img", "convert", "-p", "-f", "raw", "-O", "qcow2", str(temp_raw), str(disk_path)],
            dry_run=False,
        )
        run_pipeline.assert_not_called()
        restore_owner.assert_called_once_with(disk_path)
        restore_tree.assert_called_once_with(disk_path.parent)

    def test_cmd_import_helper_uses_real_gpt_geometry_for_compaction(self):
        disk_path = self.root / self.vm_config["disk"]["path"]
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz")
        temp_root = self.root / "tmp-import-geometry"

        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.import_dev, "validate_import_source",
                 return_value={
                     "path": "/dev/sdz",
                     "size": 250 * 1024**3,
                     "model": "USB",
                     "mountpoints": [],
                     "children": [{"type": "part", "start": 2048, "size": 5 * 1024**3}],
                     "logical_sector_size": 512,
                     "pttype": "gpt",
                     "is_root_disk": False,
                 },
             ), \
             mock.patch.object(vmctl.disk_inspect, "maybe_read_gpt_geometry",
                 return_value={
                     "gpt_partition_entry_count": 1024,
                     "gpt_partition_entry_size": 128,
                     "gpt_first_usable_lba": 34,
                 },
             ) as read_geometry, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd, \
             mock.patch.object(vmctl.runtime, "run_progress") as run_progress, \
             mock.patch.object(tempfile, "mkdtemp", return_value=str(temp_root)), \
             mock.patch.object(vmctl.runtime, "run_pipeline") as run_pipeline, \
             mock.patch.object(vmctl.flash, "maybe_restore_sudo_owner") as restore_owner, \
             mock.patch.object(vmctl.flash, "maybe_restore_sudo_owner_tree") as restore_tree:
            exit_code = self.vmctl.cmd_import_helper(args)

        self.assertEqual(exit_code, 0)
        read_geometry.assert_called_once_with("/dev/sdz", 512)
        backup_bytes = 512 * (1 + ((1024 * 128) // 512))
        expected_count = self.vmctl.round_up((2048 * 512) + (5 * 1024**3) + backup_bytes, 1024**2)
        temp_raw = temp_root / "source.raw"
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(
            executed[0],
            [
                "dd",
                "if=/dev/sdz",
                f"of={temp_raw}",
                "iflag=fullblock,count_bytes",
                f"count={expected_count}",
                "bs=4M",
                "conv=sparse",
                "status=progress",
            ],
        )
        self.assertEqual(executed[1], ["sgdisk", "-e", str(temp_raw)])
        self.assertEqual(executed[2], ["sgdisk", "-v", str(temp_raw)])
        run_progress.assert_called_once_with(
            ["qemu-img", "convert", "-p", "-f", "raw", "-O", "qcow2", str(temp_raw), str(disk_path)],
            dry_run=False,
        )
        run_pipeline.assert_not_called()
        restore_owner.assert_called_once_with(disk_path)
        restore_tree.assert_called_once_with(disk_path.parent)

    def test_validate_import_source_rejects_mounted_disk(self):
        with mock.patch.object(vmctl.disk_inspect, "inspect_block_device_basic",
            return_value={
                "path": "/dev/sdz",
                "size": 16 * 1024**3,
                "model": "USB",
                "mountpoints": ["/media/usb"],
                "children": [],
                "logical_sector_size": 512,
                "pttype": None,
                "is_root_disk": False,
            },
        ):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.validate_import_source("/dev/sdz")

    def test_suggested_import_bytes_trims_trailing_space_for_dos(self):
        import_bytes, compacted = self.vmctl.suggested_import_bytes(
            {
                "size": 250 * 1024**3,
                "pttype": "dos",
                "logical_sector_size": 512,
                "children": [
                    {"type": "part", "start": 2048, "size": 5 * 1024**3},
                    {"type": "part", "start": 6 * 1024**3 // 512, "size": 2 * 1024**3},
                ],
            }
        )

        self.assertEqual(import_bytes, 8 * 1024**3)
        self.assertEqual(compacted, True)

    def test_suggested_import_bytes_compacts_gpt_and_keeps_backup_room(self):
        import_bytes, compacted = self.vmctl.suggested_import_bytes(
            {
                "size": 250 * 1024**3,
                "pttype": "gpt",
                "logical_sector_size": 512,
                "children": [{"type": "part", "start": 2048, "size": 5 * 1024**3}],
            }
        )

        self.assertEqual(import_bytes, self.vmctl.round_up((2048 * 512) + (5 * 1024**3) + (33 * 512), 1024**2))
        self.assertEqual(compacted, True)

    def test_suggested_import_bytes_uses_actual_gpt_entry_array_size(self):
        import_bytes, compacted = self.vmctl.suggested_import_bytes(
            {
                "size": 250 * 1024**3,
                "pttype": "gpt",
                "logical_sector_size": 512,
                "gpt_partition_entry_count": 1024,
                "gpt_partition_entry_size": 128,
                "gpt_first_usable_lba": 34,
                "children": [{"type": "part", "start": 2048, "size": 5 * 1024**3}],
            }
        )

        backup_bytes = 512 * (1 + ((1024 * 128) // 512))
        self.assertEqual(import_bytes, self.vmctl.round_up((2048 * 512) + (5 * 1024**3) + backup_bytes, 1024**2))
        self.assertEqual(compacted, True)

    def test_suggested_import_bytes_uses_first_usable_lba_for_gpt_tail_gap(self):
        import_bytes, compacted = self.vmctl.suggested_import_bytes(
            {
                "size": 250 * 1024**3,
                "pttype": "gpt",
                "logical_sector_size": 512,
                "gpt_partition_entry_count": 128,
                "gpt_partition_entry_size": 128,
                "gpt_first_usable_lba": 2048,
                "children": [
                    {"type": "part", "start": 4096, "size": 4 * 1024**3},
                    {"type": "part", "start": 8392704, "size": 58716088 * 512},
                ],
            }
        )

        backup_bytes = (2048 - 1) * 512
        last_partition_end = (8392704 * 512) + (58716088 * 512)
        self.assertEqual(import_bytes, self.vmctl.round_up(last_partition_end + backup_bytes, 1024**2))
        self.assertEqual(compacted, True)


if __name__ == "__main__":
    unittest.main()
