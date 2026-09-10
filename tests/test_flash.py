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
import vmctl.cli  # noqa: E402
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
        stdin = mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=False)
        stdin.start()
        self.addCleanup(stdin.stop)

    def test_cmd_flash_requires_matching_confirmation(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device="/dev/sdz", confirm_device="/dev/sdy", dry_run=True)

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_flash(args)

    def test_expansion_flags_and_noninteractive_default_reach_sudo_helper(self):
        self.create_disk()
        info = {"size": 1024**3, "model": "USB"}
        parser = vmctl.cli.build_parser()
        for flags, tty, expected in (([], True, None), ([], False, "--no-expand"),
                                     (["--expand"], False, "--expand"),
                                     (["--no-expand"], True, "--no-expand")):
            with self.subTest(flags=flags, tty=tty), \
                 mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=tty), \
                 mock.patch.object(vmctl.runtime, "require_command"), \
                 mock.patch.object(vmctl.flash, "validate_flash_target", return_value=(info, "gpt", 1024)), \
                 mock.patch.object(vmctl.runtime, "run") as run:
                args = parser.parse_args(["--dry-run", "flash", self.vm_name, "--device", "/dev/sdz", "--confirm-device", "/dev/sdz", *flags])
                self.vmctl.cmd_flash(args)
                cmd = run.call_args.args[0]
                self.assertEqual([flag for flag in cmd if flag in {"--expand", "--no-expand"}], [expected] if expected else [])

    def test_expansion_flags_are_exclusive_in_cli_and_internal_helper(self):
        argv = ["--device", "/dev/sdz", "--confirm-device", "/dev/sdz", "--expand", "--no-expand"]
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                vmctl.cli.build_parser().parse_args(["flash", self.vm_name, *argv])
            with self.assertRaises(SystemExit):
                vmctl.cli.dispatch_internal("flash-helper", ["--vm", self.vm_name, *argv])

    def test_expansion_option_is_parsed_by_internal_helper(self):
        for flag, expected in (("--expand", True), ("--no-expand", False)):
            with self.subTest(flag=flag), mock.patch.object(vmctl.flash, "cmd_flash_helper", return_value=0) as helper:
                vmctl.cli.dispatch_internal("flash-helper", ["--vm", self.vm_name, "--device", "/dev/sdz", "--confirm-device", "/dev/sdz", flag])
                self.assertIs(helper.call_args.args[0].expand, expected)

    def test_gpt_command_can_run_silently(self):
        with mock.patch.object(vmctl.runtime.subprocess, "run") as execute, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            vmctl.runtime.run(["sgdisk", "-e", "/dev/sdz"], quiet=True, show_command=False)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(execute.call_args.kwargs["stdout"], vmctl.runtime.subprocess.DEVNULL)
        self.assertEqual(execute.call_args.kwargs["stderr"], vmctl.runtime.subprocess.DEVNULL)

    def test_quiet_run_retains_error_output_without_printing_it(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             self.assertRaises(vmctl.flash.subprocess.CalledProcessError) as error:
            vmctl.runtime.run(
                [sys.executable, "-c", "import sys; print('volume details'); print('volume is hibernated', file=sys.stderr); sys.exit(3)"],
                quiet=True, show_command=False, capture_error_output=True,
            )
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(error.exception.stdout, "volume details\n")
        self.assertEqual(error.exception.stderr, "volume is hibernated\n")

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
                "--no-expand",
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
        grow.assert_called_once_with("/dev/sdz", disk_path.parent / "flash-partitions.sfdisk", expand=None)
        run_cmd.assert_any_call(["sgdisk", "-e", "/dev/sdz"], dry_run=False, quiet=True, show_command=False)

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
            "label": "gpt", "lastlba": 8388607, "sectorsize": 512,
            "partitions": [
                {"node": self.node, "start": 4096, "size": 8192,
                 "type": "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
                 "uuid": "data-uuid", "name": "Windows", "attrs": "RequiredPartition"},
                {"node": self.device + "p1", "start": 2048, "size": 1024,
                 "type": "efi", "uuid": "efi-uuid"},
            ],
        }
        self.new_size = self.table["lastlba"] - 4096 + 1
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
            (vmctl.flash.time, "sleep", {}),
        ):
            patcher = mock.patch.object(target, name, **kwargs)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def grow(self, expand=True):
        vmctl.flash.grow_flashed_ntfs(self.device, self.backup, expand=expand)

    def execute(self, cmd, **kwargs):
        if cmd[0] == "sfdisk":
            self.partition_expanded = "-N" in cmd

    def restore_command(self):
        return ["sfdisk", "--wipe", "never", "--wipe-partitions", "never", self.device]

    def commands(self):
        return [call.args[0] for call in self.run.call_args_list if call.args[0][0] != "udevadm"]

    def settles(self):
        return [call.args[0] for call in self.run.call_args_list if call.args[0][0] == "udevadm"]

    def test_expands_last_by_position_preserving_other_partition_metadata(self):
        self.grow()
        commands = self.commands()
        resize = ["sfdisk", "--wipe", "never", "--wipe-partitions", "never", "-N", "3", self.device]
        self.assertLess(commands.index(["ntfsresize", "--check", self.node]), commands.index(resize))
        self.assertLess(commands.index(["ntfsresize", "--no-action", self.node]), commands.index(resize))
        self.run.assert_any_call(resize, stdin_text=f"size={self.new_size}\n")
        self.assertEqual(commands[-2:], [["ntfsresize", "--no-action", self.node], ["ntfsresize", self.node]])
        self.run.assert_any_call(["ntfsresize", self.node], stdin_text="y\n")
        self.assertEqual(self.backup.read_text(), "label: gpt\noriginal partition table\n")
        self.assertFalse(any("--force" in cmd for cmd in commands))

    def test_answer_no_or_empty_preserves_partition_and_filesystem(self):
        for answer in ("n", "", "maybe"):
            with self.subTest(answer=answer), \
                 mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=True), \
                 mock.patch("builtins.input", return_value=answer) as prompt:
                self.run.reset_mock()
                self.grow(expand=None)
                self.assertFalse(self.partition_expanded)
                self.assertFalse(self.backup.exists())
                self.assertFalse(any(cmd[0] == "sfdisk" for cmd in self.commands()))
                self.assertNotIn(["ntfsresize", self.node], self.commands())
                self.assertIn("4.0 GiB unallocated", prompt.call_args.args[0])
                self.assertIn("nvme9n1p3, NTFS", prompt.call_args.args[0])
                self.assertTrue(prompt.call_args.args[0].endswith("[y/N] "))

    def test_answer_yes_expands_partition_and_filesystem(self):
        with mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input", return_value="y") as prompt:
            self.grow(expand=None)
        prompt.assert_called_once()
        self.assertTrue(self.partition_expanded)
        self.assertIn(["ntfsresize", self.node], self.commands())

    def test_eof_preserves_original_sizes(self):
        with mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=EOFError):
            self.grow(expand=None)
        self.assertFalse(self.partition_expanded)
        self.assertFalse(self.backup.exists())

    def test_expand_flag_skips_prompt_even_without_tty(self):
        with mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=False), \
             mock.patch("builtins.input") as prompt:
            self.grow(expand=True)
        prompt.assert_not_called()
        self.assertTrue(self.partition_expanded)

    def test_no_expand_flag_and_noninteractive_default_never_ask_or_resize(self):
        for expand, tty in ((False, True), (False, False), (None, False)):
            with self.subTest(expand=expand, tty=tty), \
                 mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=tty), \
                 mock.patch("builtins.input") as prompt, \
                 mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.grow(expand=expand)
                prompt.assert_not_called()
                self.assertEqual(self.commands(), [])
                self.assertIn("4.0 GiB left unallocated", stdout.getvalue())
                self.assertIn("expand this copy later", stdout.getvalue())
                self.assertIn("ntfsresize (dry-run first)", stdout.getvalue())
                self.assertNotIn("--expand", stdout.getvalue())
                self.assertEqual(len(stdout.getvalue().splitlines()), 1)

    def test_below_threshold_never_asks(self):
        self.table["lastlba"] = 99999
        with mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input") as prompt:
            self.grow(expand=None)
        prompt.assert_not_called()
        self.assertEqual(self.commands(), [])

    def test_non_ntfs_is_never_offered(self):
        for fstype, expand, tty in (("ext4", None, True), ("BitLocker", None, True),
                                   ("", True, True), ("ext4", None, False),
                                   ("BitLocker", False, False)):
            with self.subTest(fstype=fstype, expand=expand, tty=tty), \
                 mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=tty), \
                 mock.patch("builtins.input") as prompt, \
                 mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.fstype = fstype
                self.run.reset_mock()
                self.grow(expand=expand)
                prompt.assert_not_called()
                self.assertFalse(any(cmd[0] in {"ntfsresize", "sfdisk"} for cmd in self.commands()))
                self.assertIn("expansion supports NTFS only", stdout.getvalue())

    def test_unrecognized_blkid_signature_is_skipped(self):
        output = self.run_output.side_effect
        def probe(cmd):
            if cmd[0] == "blkid":
                raise vmctl.flash.subprocess.CalledProcessError(2, cmd)
            return output(cmd)
        self.run_output.side_effect = probe
        with mock.patch("builtins.input") as prompt, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.grow()
        prompt.assert_not_called()
        self.assertEqual(self.commands(), [])
        self.assertIn("unknown filesystem", stdout.getvalue())

    def test_failed_preliminary_ntfs_dry_run_never_offers_or_modifies_partition(self):
        def run(cmd, **kwargs):
            self.execute(cmd, **kwargs)
            if "--no-action" in cmd:
                raise vmctl.flash.subprocess.CalledProcessError(1, cmd)
        self.run.side_effect = run
        with mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input") as prompt:
            self.grow(expand=None)
        prompt.assert_not_called()
        self.assertFalse(self.partition_expanded)
        self.assertFalse(self.backup.exists())
        self.assertFalse(any(cmd[0] == "sfdisk" for cmd in self.commands()))

    def test_ntfs_precheck_warning_reports_stderr_or_stdout(self):
        for output, stderr, reason in (("banner", "Volume is hibernated.\nShut down Windows fully.", "Volume is hibernated. Shut down Windows fully."),
                                       ("Unsupported NTFS feature", "", "Unsupported NTFS feature")):
            with self.subTest(stderr=stderr):
                def run(cmd, **kwargs):
                    if "--check" in cmd:
                        raise vmctl.flash.subprocess.CalledProcessError(1, cmd, output=output, stderr=stderr)
                    self.execute(cmd, **kwargs)
                self.run.side_effect = run
                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                     mock.patch("builtins.input") as prompt:
                    self.grow()
                prompt.assert_not_called()
                self.assertIn(reason, stdout.getvalue())
                self.assertEqual(len(stdout.getvalue().splitlines()), 1)
                self.assertFalse(self.partition_expanded)
                self.assertFalse(self.backup.exists())

    def test_full_flash_repairs_gpt_before_asking_and_resizes_in_order(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device=self.device, confirm_device=self.device, force_target=False, expand=None)
        def answer(prompt):
            self.assertIn(["sgdisk", "-e", self.device], self.commands())
            self.assertTrue(any(cmd[0] == "qemu-img" for cmd in self.commands()))
            self.assertFalse(self.partition_expanded)
            return "y"
        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target", return_value=({"size": 8 * 1024**3}, "gpt", 1024**3)), \
             mock.patch.object(vmctl.disk_inspect, "partition_layout", return_value="gpt"), \
             mock.patch.object(vmctl.flash.sys.stdin, "isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=answer):
            self.assertEqual(vmctl.flash.cmd_flash_helper(args), 0)
        commands = self.commands()
        gpt = commands.index(["sgdisk", "-e", self.device])
        partition = next(i for i, cmd in enumerate(commands) if cmd[0] == "sfdisk")
        dry_runs = [i for i, cmd in enumerate(commands) if "--no-action" in cmd]
        resize = commands.index(["ntfsresize", self.node])
        self.assertLess(gpt, dry_runs[0])
        self.assertLess(dry_runs[0], partition)
        self.assertLess(partition, dry_runs[1])
        self.assertLess(dry_runs[1], resize)
        self.assertEqual(commands[-1], ["sync"])

    def test_full_flash_no_expand_still_repairs_gpt(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, device=self.device, confirm_device=self.device, force_target=False, expand=False)
        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.flash, "validate_flash_target", return_value=({"size": 8 * 1024**3}, "gpt", 1024**3)), \
             mock.patch.object(vmctl.disk_inspect, "partition_layout", return_value="gpt"), \
             mock.patch("builtins.input") as prompt:
            self.assertEqual(vmctl.flash.cmd_flash_helper(args), 0)
        prompt.assert_not_called()
        self.assertIn(["sgdisk", "-e", self.device], self.commands())
        self.assertFalse(self.partition_expanded)
        self.assertNotIn(["ntfsresize", self.node], self.commands())

    def test_skips_recovery_partition_at_end(self):
        self.table["partitions"][0]["type"] = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
        self.grow()
        self.assertEqual(self.commands(), [])
        self.assertFalse(self.backup.exists())

    def test_skips_bitlocker_and_unsupported_filesystems(self):
        for fstype in ("BitLocker", "ext4", ""):
            with self.subTest(fstype=fstype):
                self.fstype = fstype
                self.table_reads = 0
                self.run.reset_mock()
                self.grow()
                self.assertEqual(self.commands(), [])

    def test_skips_partition_already_using_all_space(self):
        self.table["partitions"][0]["size"] = self.new_size
        self.grow()
        self.assertEqual(self.commands(), [])

    def test_missing_ntfsresize_does_not_change_partition(self):
        self.which.return_value = None
        self.grow()
        self.assertEqual(self.commands(), [["blockdev", "--rereadpt", self.device]])

    def test_rejects_mounted_target(self):
        self.info["mountpoints"] = ["/media/windows"]
        with self.assertRaisesRegex(self.vmctl.VMError, "mounted.*udisksctl.*disable desktop automount"):
            self.grow()
        self.assertEqual(self.run_output.call_count, 2)

    def test_failed_initial_reread_stops_before_inspection(self):
        self.run.side_effect = vmctl.flash.subprocess.CalledProcessError(1, "blockdev")
        with self.assertRaisesRegex(self.vmctl.VMError, "did not reread.*5 attempts"):
            self.grow()
        self.assertEqual(self.run_output.call_count, 2)
        rereads = [cmd for cmd in self.commands() if cmd[0] == "blockdev"]
        self.assertEqual(len(rereads), 5)
        self.assertEqual(len(self.settles()), 5)
        self.assertEqual(self.sleep.call_count, 4)
        self.assertFalse(any(cmd[0] == "sfdisk" for cmd in self.commands()))

    def test_busy_reread_is_retried_after_udev_settle(self):
        # Live case: udev/udisks probe the partitions right after sgdisk -e and
        # the second BLKRRPART returns EBUSY for about a second.
        failures = {"left": 1}
        def run(cmd, **kwargs):
            self.execute(cmd, **kwargs)
            if cmd[0] == "blockdev" and failures["left"]:
                failures["left"] -= 1
                raise vmctl.flash.subprocess.CalledProcessError(1, cmd)
        self.run.side_effect = run
        self.grow()
        commands = self.commands()
        self.assertIn(["ntfsresize", self.node], commands)
        self.assertEqual(self.settles()[0], ["udevadm", "settle", "--timeout=15"])
        self.assertEqual(commands.index(["blockdev", "--rereadpt", self.device]) + 1,
                         commands.index(["blockdev", "--rereadpt", self.device], 1))
        self.sleep.assert_called_once_with(1)

    def test_busy_reread_with_automounted_partition_names_the_mount(self):
        def run(cmd, **kwargs):
            self.execute(cmd, **kwargs)
            if cmd[0] == "blockdev":
                self.info["mountpoints"] = ["/media/lab/Windows"]
                raise vmctl.flash.subprocess.CalledProcessError(1, cmd)
        self.run.side_effect = run
        with self.assertRaisesRegex(self.vmctl.VMError, "mounted.*/media/lab/Windows.*udisksctl"):
            self.grow()
        self.sleep.assert_not_called()
        self.assertFalse(any(cmd[0] == "sfdisk" for cmd in self.commands()))

    def test_missing_udevadm_skips_settle(self):
        self.which.side_effect = lambda name: None if name == "udevadm" else "/usr/bin/" + name
        self.grow()
        self.assertEqual(self.settles(), [])
        self.assertIn(["ntfsresize", self.node], self.commands())

    def test_dirty_ntfs_stops_before_partition_write(self):
        def run(cmd, **kwargs):
            self.execute(cmd, **kwargs)
            if cmd[0] == "ntfsresize":
                raise vmctl.flash.subprocess.CalledProcessError(1, cmd)
        self.run.side_effect = run
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
            if "--no-action" in cmd and self.partition_expanded:
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
                if self.partition_expanded:  # every retry fails until the table is restored
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
            if "--no-action" in cmd and self.partition_expanded:
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
            if "--no-action" in cmd and self.partition_expanded:
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
