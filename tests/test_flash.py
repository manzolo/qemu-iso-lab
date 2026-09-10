import argparse
import io
import json
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
    def setUp(self):
        super().setUp()
        probe = mock.patch.object(vmctl.disk_inspect, "partition_layout", return_value="dos")
        self.layout_probe = probe.start()
        self.addCleanup(probe.stop)

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
        self.layout_probe.return_value = "gpt"
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
             mock.patch.object(vmctl.flash, "grow_flashed_ntfs") as grow, \
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
        self.assertEqual(executed[3], ["sync"])
        self.layout_probe.assert_called_once_with(Path("/dev/sdz"))
        grow.assert_called_once_with("/dev/sdz", disk_path.parent / "flash-partitions.sfdisk")

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
        self.assertEqual(executed[2], ["sgdisk", "--zap-all", "/dev/sdz"])
        self.assertEqual(executed[3], ["wipefs", "-a", "-f", "/dev/sdz"])
        self.assertEqual(executed[4], ["blockdev", "--rereadpt", "/dev/sdz"])

    def test_helper_repairs_inherited_gpt_even_when_image_matches_target_size(self):
        self.create_disk()
        self.layout_probe.return_value = "gpt"
        info = {"size": 64 * 1024**3, "children": [], "signatures": []}
        for force in (False, True):
            with self.subTest(force=force), \
                 mock.patch.object(os, "geteuid", return_value=0), \
                 mock.patch.object(vmctl.runtime, "require_command"), \
                 mock.patch.object(vmctl.flash, "validate_flash_target", return_value=(info, None, info["size"])), \
                 mock.patch.object(vmctl.disk_inspect, "inspect_block_device_basic", side_effect=AssertionError("GPT detection must not depend on lsblk cache")), \
                 mock.patch.object(vmctl.flash, "grow_flashed_ntfs") as grow, \
                 mock.patch.object(vmctl.runtime, "run") as run:
                args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=force)
                self.assertEqual(self.vmctl.cmd_flash_helper(args), 0)
                self.assertIn(["sgdisk", "-e", args.device], [call.args[0] for call in run.call_args_list])
                grow.assert_called_once()

    def test_missing_gpt_tools_fails_before_copy(self):
        self.create_disk()
        def require(command):
            if command == "sgdisk":
                raise self.vmctl.VMError("Missing command: sgdisk")
        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command", side_effect=require), \
             mock.patch.object(vmctl.flash, "validate_flash_target", return_value=({"size": 1024}, None, 1024)), \
             mock.patch.object(vmctl.runtime, "run") as run:
            args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=True)
            with self.assertRaisesRegex(self.vmctl.VMError, "Missing command"):
                self.vmctl.cmd_flash_helper(args)
            run.assert_not_called()

    def test_post_flash_failure_reports_copied_image_and_syncs(self):
        self.create_disk()
        self.layout_probe.return_value = "gpt"
        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target", return_value=({"size": 1024}, None, 1024)), \
             mock.patch.object(vmctl.flash, "grow_flashed_ntfs", side_effect=self.vmctl.VMError("unsafe NTFS")), \
             mock.patch.object(vmctl.runtime, "run") as run:
            args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdz", force_target=False)
            with self.assertRaisesRegex(self.vmctl.VMError, "copied.*post-flash.*unsafe NTFS"):
                self.vmctl.cmd_flash_helper(args)
            self.assertEqual(run.call_args.args[0], ["sync"])

    def test_cmd_flash_helper_force_target_skips_zap_when_target_is_not_gpt(self):
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
                         "children": [{"path": "/dev/sdz1"}],
                         "signatures": [{"type": "ext4"}],
                         "pttype": "dos",
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
        self.assertNotIn(["sgdisk", "--zap-all", "/dev/sdz"], executed)

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


class FlashExpansionTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.device = "/dev/nvme9n1"
        self.node = self.device + "p3"
        self.backup = self.root / "flash-partitions.sfdisk"
        self.table = {
            "label": "gpt", "lastlba": 99999, "sectorsize": 512,
            "partitions": [
                {"node": self.node, "start": 4096, "size": 8192,
                 "type": "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
                 "uuid": "data-uuid", "name": "Windows", "attrs": "RequiredPartition"},
                {"node": self.device + "p1", "start": 2048, "size": 1024,
                 "type": "efi", "uuid": "efi-uuid"},
            ],
        }
        self.new_size = 99999 - 4096 + 1
        self.info = {"mountpoints": [], "is_root_disk": False}
        self.fstype = "ntfs"
        self.kernel_size = self.new_size * 512
        self.table_reads = 0
        self.partition_expanded = False
        self.reformat_table = False
        self.geometry_change = {}

        def output(cmd):
            if cmd == ["sfdisk", "--json", self.device]:
                table = json.loads(json.dumps(self.table))
                if self.partition_expanded:
                    table["partitions"][0]["size"] = self.new_size
                    table["partitions"][0].update(self.geometry_change)
                    if self.reformat_table:
                        for part in table["partitions"]:
                            part["type"] = part["type"].lower()
                            part["uuid"] = part["uuid"].upper()
                            part["name"] = "reformatted name"
                            part["attrs"] = "reformatted attributes"
                        table["partitions"].reverse()
                self.table_reads += 1
                return json.dumps({"partitiontable": table})
            if cmd == ["sfdisk", "--dump", self.device]:
                return "label: gpt\noriginal partition table\n"
            if cmd == ["blkid", "-p", "-s", "TYPE", "-o", "value", self.node]:
                return self.fstype
            if cmd == ["blockdev", "--getsize64", self.node]:
                return str(self.kernel_size)
            self.fail(f"Unexpected command: {cmd}")

        for target, name, kwargs in (
            (vmctl.runtime, "run", {"side_effect": self.execute}),
            (vmctl.runtime, "run_output", {"side_effect": output}),
            (vmctl.disk_inspect, "inspect_block_device_basic", {"return_value": self.info}),
            (vmctl.flash.shutil, "which", {"return_value": "/usr/bin/ntfsresize"}),
        ):
            patcher = mock.patch.object(target, name, **kwargs)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def grow(self):
        vmctl.flash.grow_flashed_ntfs(self.device, self.backup)

    def execute(self, cmd, **kwargs):
        if cmd[0] == "sfdisk":
            self.partition_expanded = "-N" in cmd

    def restore_command(self):
        return ["sfdisk", "--wipe", "never", "--wipe-partitions", "never", self.device]

    def commands(self):
        return [call.args[0] for call in self.run.call_args_list]

    def test_expands_last_by_position_preserving_other_partition_metadata(self):
        self.grow()
        commands = self.commands()
        resize = ["sfdisk", "--wipe", "never", "--wipe-partitions", "never", "-N", "3", self.device]
        self.assertLess(commands.index(["ntfsresize", "--check", self.node]), commands.index(resize))
        self.run.assert_any_call(resize, stdin_text=f"size={self.new_size}\n")
        self.assertEqual(commands[-2:], [["ntfsresize", "--no-action", self.node], ["ntfsresize", self.node]])
        self.run.assert_any_call(["ntfsresize", self.node], stdin_text="y\n")
        self.assertEqual(self.backup.read_text(), "label: gpt\noriginal partition table\n")
        self.assertFalse(any("--force" in cmd for cmd in commands))

    def test_skips_recovery_partition_at_end(self):
        self.table["partitions"][0]["type"] = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
        self.grow()
        self.assertEqual(self.commands(), [["blockdev", "--rereadpt", self.device]])
        self.assertFalse(self.backup.exists())

    def test_skips_bitlocker_and_unsupported_filesystems(self):
        for fstype in ("BitLocker", "ext4", ""):
            with self.subTest(fstype=fstype):
                self.fstype = fstype
                self.table_reads = 0
                self.run.reset_mock()
                self.grow()
                self.assertEqual(self.commands(), [["blockdev", "--rereadpt", self.device]])

    def test_skips_partition_already_using_all_space(self):
        self.table["partitions"][0]["size"] = self.new_size
        self.grow()
        self.assertEqual(self.commands(), [["blockdev", "--rereadpt", self.device]])

    def test_missing_ntfsresize_does_not_change_partition(self):
        self.which.return_value = None
        self.grow()
        self.assertEqual(self.commands(), [["blockdev", "--rereadpt", self.device]])

    def test_rejects_mounted_target(self):
        self.info["mountpoints"] = ["/media/windows"]
        with self.assertRaisesRegex(self.vmctl.VMError, "mounted.*udisksctl.*disable desktop automount"):
            self.grow()
        self.run_output.assert_not_called()

    def test_failed_initial_reread_stops_before_inspection(self):
        self.run.side_effect = vmctl.flash.subprocess.CalledProcessError(1, "blockdev")
        with self.assertRaises(vmctl.flash.subprocess.CalledProcessError):
            self.grow()
        self.run_output.assert_not_called()

    def test_dirty_ntfs_stops_before_partition_write(self):
        def run(cmd, **kwargs):
            self.execute(cmd, **kwargs)
            if cmd[0] == "ntfsresize":
                raise vmctl.flash.subprocess.CalledProcessError(1, cmd)
        self.run.side_effect = run
        with self.assertRaises(vmctl.flash.subprocess.CalledProcessError):
            self.grow()
        self.assertFalse(self.backup.exists())
        self.assertFalse(any(cmd[0] == "sfdisk" for cmd in self.commands()))

    def test_stale_kernel_size_stops_before_filesystem_resize(self):
        self.kernel_size = 8192 * 512
        with self.assertRaisesRegex(self.vmctl.VMError, "stale"):
            self.grow()
        self.assertNotIn(["ntfsresize", self.node], self.commands())
        self.assertIn(self.restore_command(), self.commands())
        self.assertFalse(self.partition_expanded)

    def test_failed_dry_run_never_writes_filesystem(self):
        def run(cmd, **kwargs):
            self.execute(cmd, **kwargs)
            if "--no-action" in cmd:
                raise vmctl.flash.subprocess.CalledProcessError(1, cmd)
        self.run.side_effect = run
        with self.assertRaisesRegex(self.vmctl.VMError, "Original partition geometry restored"):
            self.grow()
        self.assertNotIn(["ntfsresize", self.node], self.commands())
        self.run.assert_any_call(self.restore_command(), stdin_text="label: gpt\noriginal partition table\n")
        self.assertFalse(self.partition_expanded)

    def test_failed_reread_after_partition_write_never_resizes_filesystem(self):
        rereads = 0
        def run(cmd, **kwargs):
            nonlocal rereads
            self.execute(cmd, **kwargs)
            if cmd[0] == "blockdev":
                rereads += 1
                if rereads == 2:
                    raise vmctl.flash.subprocess.CalledProcessError(1, cmd)
        self.run.side_effect = run
        with self.assertRaisesRegex(self.vmctl.VMError, "Original partition geometry restored"):
            self.grow()
        self.assertNotIn(["ntfsresize", self.node], self.commands())
        self.assertFalse(self.partition_expanded)

    def test_cosmetic_json_changes_do_not_block_filesystem_resize(self):
        self.reformat_table = True
        self.grow()
        self.assertIn(["ntfsresize", self.node], self.commands())

    def test_unexpected_geometry_stops_resize_and_refuses_blind_restore(self):
        for field, value in (("start", 8192), ("size", 4096), ("type", "recovery"), ("uuid", "other-uuid")):
            with self.subTest(field=field):
                self.partition_expanded = False
                self.geometry_change = {field: value}
                self.run.reset_mock()
                with self.assertRaisesRegex(self.vmctl.VMError, "geometry changed unexpectedly"):
                    self.grow()
                self.assertNotIn(["ntfsresize", self.node], self.commands())
                self.assertNotIn(self.restore_command(), self.commands())

    def test_failed_filesystem_write_never_restores_smaller_partition(self):
        def run(cmd, **kwargs):
            self.execute(cmd, **kwargs)
            if cmd == ["ntfsresize", self.node]:
                raise vmctl.flash.subprocess.CalledProcessError(1, cmd)
        self.run.side_effect = run
        with self.assertRaisesRegex(self.vmctl.VMError, "filesystem may have been modified.*Do not restore"):
            self.grow()
        self.assertNotIn(self.restore_command(), self.commands())
        self.assertTrue(self.partition_expanded)

    def test_automount_during_dry_run_stops_resize_and_restore(self):
        def run(cmd, **kwargs):
            self.execute(cmd, **kwargs)
            if "--no-action" in cmd:
                self.info["mountpoints"] = ["/media/windows"]
        self.run.side_effect = run
        with self.assertRaisesRegex(self.vmctl.VMError, "Automatic partition restore did not complete.*automount"):
            self.grow()
        self.assertNotIn(["ntfsresize", self.node], self.commands())
        self.assertNotIn(self.restore_command(), self.commands())
        self.assertTrue(self.partition_expanded)

    def test_automount_during_check_stops_before_partition_write(self):
        def run(cmd, **kwargs):
            self.execute(cmd, **kwargs)
            if "--check" in cmd:
                self.info["mountpoints"] = ["/media/windows"]
        self.run.side_effect = run
        with self.assertRaisesRegex(self.vmctl.VMError, "disable desktop automount"):
            self.grow()
        self.assertFalse(any(cmd[0] == "sfdisk" for cmd in self.commands()))

    def test_restore_failure_preserves_original_error_and_backup_location(self):
        def run(cmd, **kwargs):
            if "--no-action" in cmd:
                raise self.vmctl.VMError("NTFS test failed")
            if cmd == self.restore_command():
                raise self.vmctl.VMError("restore write failed")
            self.execute(cmd, **kwargs)
        self.run.side_effect = run
        with self.assertRaisesRegex(self.vmctl.VMError, "NTFS test failed.*restore write failed.*flash-partitions.sfdisk"):
            self.grow()
        self.assertTrue(self.partition_expanded)


if __name__ == "__main__":
    unittest.main()
