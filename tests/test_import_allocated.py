import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vmctl import cli, host_setup, import_allocated as allocated, import_dev, lifecycle, runtime
from vmctl.errors import VMError


class AllocatedImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "device"
        self.source.write_bytes(b"B" * 512 + b"A" * 512 + b"deleted!" * 64 + b"T" * 512)
        self.target = self.root / "disk.qcow2"
        self.target.write_bytes(b"original VM disk")
        self.work = self.target.with_name(self.target.name + ".allocated-import")
        self.part = {"path": str(self.root / "partition"), "start": 512, "size": 1024, "fstype": "ext4"}
        self.info = {"size": 2048, "logical_sector_size": 512, "pttype": "gpt",
                     "children": [{"path": self.part["path"], "type": "part", "start": 1, "size": 1024, "fstype": "ext4"}]}
        self.table = {"label": "gpt", "unit": "sectors", "sectorsize": 512,
                      "partitions": [{"node": self.part["path"], "start": 1, "size": 2, "type": "linux"}]}
        self.vm = {"disk": {"format": "qcow2", "path": str(self.target)}}
        self.args = argparse.Namespace(vm="test", device=str(self.source), resume=False)
        self.blocks = [allocated.Block(0, 1024, "+"), allocated.Block(1024, 512, "?"), allocated.Block(1536, 512, "+")]

    def write_map(self, path, blocks):
        path.write_text(allocated.map_text(blocks), encoding="ascii")

    def patches(self):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(allocated, "partition_plan", return_value=(self.table, [self.part])))
        stack.enter_context(mock.patch.object(allocated, "build_domain", return_value=self.blocks))
        stack.enter_context(mock.patch.object(allocated, "require_stopped"))
        stack.enter_context(mock.patch.object(runtime, "require_command"))
        stack.enter_context(mock.patch.object(import_dev, "validate_import_source", return_value=self.info))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return stack

    def fake_run(self, cmd, **kwargs):
        if cmd[0] == "ddrescue":
            raw = Path(cmd[-2])
            with raw.open("r+b") as output:
                for block in self.blocks:
                    if block.status == "+":
                        output.seek(block.start)
                        output.write(self.source.read_bytes()[block.start:block.end])
            self.write_map(Path(cmd[-1]), self.blocks)

    def fake_convert(self, cmd, **kwargs):
        shutil.copyfile(cmd[-2], cmd[-1])

    def do_import(self):
        allocated.import_disk(self.args, self.vm, self.target, self.info)

    def interrupt_import(self):
        def interrupted(cmd, **kwargs):
            self.fake_run(cmd, **kwargs)
            if cmd[0] == "ddrescue":
                raise KeyboardInterrupt()
        with self.patches(), mock.patch.object(runtime, "run", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.do_import()
        self.assertEqual(self.target.read_bytes(), b"original VM disk")
        self.assertTrue((self.work / "manifest.json").is_file())

    def test_cli_requires_explicit_allocated_mode_for_resume(self):
        args = cli.build_parser().parse_args(["import-device", "vm", "--device", "/dev/sdz", "--confirm-device", "/dev/sdz", "--resume"])
        with self.assertRaisesRegex(VMError, "requires --allocated-only"):
            import_dev.allocated_mode(args)
        args.allocated_only = True
        self.assertTrue(import_dev.allocated_mode(args))

    def test_partition_plan_uses_sfdisk_sector_geometry(self):
        with mock.patch.object(runtime, "run_output", return_value=json.dumps({"partitiontable": self.table})):
            self.assertEqual(allocated.partition_plan(str(self.source), self.info), (self.table, [self.part]))

    def test_rejects_unsupported_or_inconsistent_layouts(self):
        for change in ("extended", "overlap", "size", "kernel_offset", "4kn", "active"):
            with self.subTest(change=change):
                table = json.loads(json.dumps(self.table))
                info = json.loads(json.dumps(self.info))
                if change == "extended":
                    table["label"] = info["pttype"] = "dos"
                    table["partitions"][0]["type"] = "f"
                elif change == "overlap":
                    table["partitions"][0]["start"] = 0
                elif change == "size":
                    table["partitions"][0]["size"] = 20
                elif change == "4kn":
                    info["logical_sector_size"] = 4096
                elif change == "kernel_offset":
                    info["children"][0]["start"] = 2
                else:
                    info["children"][0]["children"] = [{"type": "crypt"}]
                with mock.patch.object(runtime, "run_output", return_value=json.dumps({"partitiontable": table})):
                    with self.assertRaises(VMError):
                        allocated.partition_plan(str(self.source), info)

    def test_domain_offsets_preserve_boot_gaps_unknown_partition_and_fs_tail(self):
        part = {**self.part, "size": 1536}
        unknown = {"path": "/dev/fake2", "start": 2560, "size": 512, "fstype": "crypto_LUKS"}
        def partclone(cmd):
            self.write_map(Path(cmd[cmd.index("--output") + 1]), [allocated.Block(0, 512, "+"), allocated.Block(512, 512, "?")])
        with mock.patch.object(runtime, "require_command"), mock.patch.object(runtime, "run", side_effect=partclone):
            result = allocated.build_domain([part, unknown], 4096, self.root)
        self.assertEqual(result, [allocated.Block(0, 1024, "+"), allocated.Block(1024, 512, "?"), allocated.Block(1536, 2560, "+")])

    def test_invalid_domain_maps_fail_closed(self):
        for data in ("", "0 ?\n0 512 ?\n1024 512 +\n", "0 ?\n0 4096 +\n", "0 ?\n0 512 -\n", "0 ?\n0 0 +\n"):
            with self.subTest(data=data):
                path = self.root / "invalid.map"
                path.write_text(data)
                with self.assertRaises(VMError):
                    allocated.read_map(path, 2048, "?+")

    def test_success_preserves_selected_bytes_and_publishes_only_after_check(self):
        def checked_run(cmd, **kwargs):
            self.assertEqual(self.target.read_bytes(), b"original VM disk")
            self.fake_run(cmd, **kwargs)
        with self.patches(), mock.patch.object(runtime, "run", side_effect=checked_run) as run, \
                mock.patch.object(runtime, "run_progress", side_effect=self.fake_convert):
            self.do_import()
        self.assertEqual(self.target.read_bytes(), b"B" * 512 + b"A" * 512 + b"\0" * 512 + b"T" * 512)
        self.assertFalse(self.work.exists())
        self.assertIn("--sparse", run.call_args_list[0].args[0])
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["ddrescue", "ddrescuelog", "qemu-img"])

    def test_unrecovered_blocks_do_not_replace_target(self):
        def incomplete(cmd, **kwargs):
            self.fake_run(cmd, **kwargs)
            if cmd[0] == "ddrescuelog":
                raise subprocess.CalledProcessError(1, cmd)
        with self.patches(), mock.patch.object(runtime, "run", side_effect=incomplete), \
                mock.patch.object(runtime, "run_progress") as convert:
            with self.assertRaisesRegex(VMError, "Import incomplete"):
                self.do_import()
        convert.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"original VM disk")
        self.assertTrue((self.work / "rescue.map").exists())

    def test_truncated_rescue_map_is_not_treated_as_success(self):
        def truncated(cmd, **kwargs):
            if cmd[0] == "ddrescue":
                self.write_map(Path(cmd[-1]), [allocated.Block(0, 512, "+")])
        with self.patches(), mock.patch.object(runtime, "run", side_effect=truncated), \
                mock.patch.object(runtime, "run_progress") as convert:
            with self.assertRaisesRegex(VMError, "does not cover"):
                self.do_import()
        convert.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"original VM disk")

    def test_changed_allocation_map_prevents_resume(self):
        self.interrupt_import()
        self.args.resume = True
        self.blocks = [allocated.Block(0, 2048, "+")]
        with self.patches(), mock.patch.object(runtime, "run") as run:
            with self.assertRaisesRegex(VMError, "partition allocation"):
                self.do_import()
        run.assert_not_called()

    def test_partclone_failure_never_starts_ddrescue(self):
        build_domain = allocated.build_domain
        for fstype in ("ntfs", "ext4"):
            with self.subTest(fstype=fstype):
                self.part["fstype"] = fstype
                scans = []

                def rejected(cmd, **kwargs):
                    self.assertEqual(cmd[0], allocated.PARTCLONE[fstype])
                    log = Path(cmd[cmd.index("--logfile") + 1])
                    log.write_text("Volume is scheduled for a check or was shutdown uncleanly")
                    scans.append(log)
                    raise subprocess.CalledProcessError(1, cmd)

                with self.patches(), mock.patch.object(allocated, "build_domain", side_effect=build_domain), \
                        mock.patch.object(runtime, "run", side_effect=rejected) as run, \
                        mock.patch.object(runtime, "run_progress") as convert:
                    with self.assertRaisesRegex(VMError, "could not map allocated blocks") as caught:
                        self.do_import()
                self.assertEqual(run.call_count, 1)
                convert.assert_not_called()
                message = str(caught.exception)
                self.assertIn(self.part["path"], message)
                self.assertIn(str(scans[-1]), message)
                self.assertIn("do not use --resume", message)
                if fstype == "ntfs":
                    self.assertIn("check the volume in Windows", message)
                self.assertIn("shutdown uncleanly", scans[-1].read_text())
                self.assertEqual(self.target.read_bytes(), b"original VM disk")
                self.assertFalse((self.work / "source.raw").exists())
                self.assertFalse((self.work / "manifest.json").exists())
                # A failed scan can be retried while the source remains unchanged.
                self.args.resume = True

    def test_resume_after_failed_scan_with_unchanged_source(self):
        with self.patches(), mock.patch.object(allocated, "build_domain", side_effect=VMError("scan failed")):
            with self.assertRaisesRegex(VMError, "scan failed"):
                self.do_import()
        self.assertTrue(list(self.work.glob("scan-*")))
        self.assertFalse((self.work / "manifest.json").exists())
        self.args.resume = True
        with self.patches(), mock.patch.object(runtime, "run", side_effect=self.fake_run), \
                mock.patch.object(runtime, "run_progress", side_effect=self.fake_convert):
            self.do_import()
        self.assertFalse(self.work.exists())
        self.assertEqual(self.target.read_bytes(), b"B" * 512 + b"A" * 512 + b"\0" * 512 + b"T" * 512)

    def test_concurrent_import_is_rejected(self):
        self.interrupt_import()
        self.args.resume = True
        with (self.work / "lock").open("a") as lock:
            allocated.fcntl.flock(lock, allocated.fcntl.LOCK_EX | allocated.fcntl.LOCK_NB)
            with self.patches(), mock.patch.object(runtime, "run") as run:
                with self.assertRaisesRegex(VMError, "Another allocated import"):
                    self.do_import()
            run.assert_not_called()

    def test_interrupt_and_resume(self):
        self.interrupt_import()
        self.args.resume = True
        with self.patches(), mock.patch.object(runtime, "run", side_effect=self.fake_run) as run, \
                mock.patch.object(runtime, "run_progress", side_effect=self.fake_convert):
            self.do_import()
        self.assertNotIn("--sparse", run.call_args_list[0].args[0])
        self.assertFalse(self.work.exists())

    def test_resume_rejects_changed_source_target_domain_and_missing_raw(self):
        self.interrupt_import()
        self.args.resume = True
        for changed in ("source", "target", "domain", "raw"):
            with self.subTest(changed=changed):
                path = {"source": self.source, "target": self.target,
                        "domain": self.work / "domain.map", "raw": self.work / "source.raw"}[changed]
                before, stat = path.read_bytes(), path.stat()
                if changed == "raw":
                    path.unlink()
                else:
                    path.write_bytes(b"X" + before[1:])
                with self.patches(), mock.patch.object(runtime, "run") as run:
                    with self.assertRaisesRegex(VMError, "Cannot resume"):
                        self.do_import()
                run.assert_not_called()
                path.write_bytes(before)
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    def test_conversion_failure_retains_old_target_and_resumable_raw(self):
        with self.patches(), mock.patch.object(runtime, "run", side_effect=self.fake_run), \
                mock.patch.object(runtime, "run_progress", side_effect=subprocess.CalledProcessError(1, "qemu-img")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.do_import()
        self.assertEqual(self.target.read_bytes(), b"original VM disk")
        self.assertTrue((self.work / "source.raw").is_file())

    def test_existing_state_is_not_deleted_by_fresh_import(self):
        self.interrupt_import()
        with self.patches(), mock.patch.object(runtime, "run") as run:
            with self.assertRaisesRegex(VMError, "already exists"):
                self.do_import()
        run.assert_not_called()
        self.assertTrue((self.work / "rescue.map").is_file())

    def test_running_vm_is_rejected(self):
        with mock.patch.object(lifecycle, "running_qemu_pid", return_value=123):
            with self.assertRaisesRegex(VMError, "Stop VM"):
                allocated.require_stopped("test", self.vm)

    def test_dependency_packages_for_supported_hosts(self):
        for distro, packages in (("ubuntu", {"gddrescue", "partclone", "fdisk"}),
                                  ("arch", {"ddrescue", "partclone", "util-linux"})):
            with self.subTest(distro=distro), mock.patch.object(host_setup, "read_os_release", return_value={"ID": distro}):
                commands = host_setup.host_install_commands()
                self.assertTrue(packages <= set(commands[-1]))


@unittest.skipUnless(all(shutil.which(tool) for tool in ("partclone.extfs", "mkfs.ext4", "e2fsck", "ddrescue", "ddrescuelog", "qemu-img")),
                     "real import tools are not installed")
class AllocatedImportIntegrationTests(unittest.TestCase):
    def test_real_ext4_domain_rescue_conversion_and_filesystem_integrity(self):
        self.roundtrip("ext4", ["e2fsck", "-f", "-n"])

    def test_real_ext4_partial_copy_and_resume(self):
        self.roundtrip("ext4", ["e2fsck", "-f", "-n"], resume=True)

    @unittest.skipUnless(all(shutil.which(tool) for tool in ("mkfs.fat", "partclone.fat", "fsck.fat", "mcopy")), "FAT tools are not installed")
    def test_real_fat_domain_and_filesystem_integrity(self):
        self.roundtrip("vfat", ["fsck.fat", "-n"])

    @unittest.skipUnless(all(shutil.which(tool) for tool in ("mkntfs", "partclone.ntfs", "ntfsfix", "ntfscp")), "NTFS tools are not installed")
    def test_real_ntfs_domain_and_filesystem_integrity(self):
        self.roundtrip("ntfs", ["ntfsfix", "-n"])

    def roundtrip(self, fstype, check, resume=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partition = root / "partition.img"
            contents = root / "files"
            contents.mkdir()
            (contents / "payload.txt").write_bytes(b"allocated payload\n" * 1000)
            with partition.open("wb") as output:
                output.truncate(32 * 1024**2)
            if fstype == "ext4":
                subprocess.run(["mkfs.ext4", "-q", "-F", "-d", str(contents), str(partition)], check=True)
            elif fstype == "vfat":
                subprocess.run(["mkfs.fat", "-F", "16", str(partition)], check=True, capture_output=True)
                subprocess.run(["mcopy", "-i", str(partition), str(contents / "payload.txt"), "::payload.txt"], check=True)
            else:
                subprocess.run(["mkntfs", "-F", "-Q", "-s", "512", str(partition)], check=True, capture_output=True)
                subprocess.run(["ntfscp", str(partition), str(contents / "payload.txt"), "/payload.txt"], check=True)
            prefix = 1024**2
            size = partition.stat().st_size + 2 * prefix
            plan = [{"path": str(partition), "start": prefix, "size": partition.stat().st_size, "fstype": fstype}]
            scratch = root / "scan"
            scratch.mkdir()
            blocks = allocated.build_domain(plan, size, scratch)
            skipped = sum(block.size for block in blocks if block.status == "?")
            self.assertGreater(skipped, 16 * 1024**2)
            free = next(block for block in blocks if block.status == "?")
            # Deleted data is nonzero, proving this is allocation-aware, not zero detection.
            with partition.open("r+b") as output:
                output.seek(free.start - prefix)
                output.write(b"deleted-data" * 32)
            source = root / "source.raw"
            source.write_bytes(b"B" * prefix + partition.read_bytes() + b"T" * prefix)
            target = root / "disk.qcow2"
            target.write_bytes(b"old target")
            vm = {"disk": {"format": "qcow2", "path": str(target)}}
            args = argparse.Namespace(vm="integration", device=str(source), resume=False)
            info = {"size": size}
            with mock.patch.object(allocated, "partition_plan", return_value=({}, plan)), \
                    mock.patch.object(allocated, "require_stopped"), \
                    mock.patch.object(import_dev, "validate_import_source", return_value=info):
                if resume:
                    run = runtime.run
                    def interrupted(cmd, **kwargs):
                        if cmd[0] == "ddrescue":
                            limited = [f"--size={size // 2}" if arg == f"--size={size}" else arg for arg in cmd]
                            run(limited, **kwargs)
                            raise KeyboardInterrupt()
                        run(cmd, **kwargs)
                    with mock.patch.object(runtime, "run", side_effect=interrupted):
                        with self.assertRaises(KeyboardInterrupt):
                            allocated.import_disk(args, vm, target, info)
                    self.assertEqual(target.read_bytes(), b"old target")
                    args.resume = True
                allocated.import_disk(args, vm, target, info)
            restored = root / "restored.raw"
            subprocess.run(["qemu-img", "convert", "-f", "qcow2", "-O", "raw", str(target), str(restored)], check=True)
            data = restored.read_bytes()
            self.assertEqual(len(data), size)
            original = source.read_bytes()
            for block in blocks:
                expected = original[block.start:block.end] if block.status == "+" else bytes(block.size)
                self.assertEqual(data[block.start:block.end], expected)
            restored_partition = root / "restored.partition"
            restored_partition.write_bytes(data[prefix:-prefix])
            subprocess.run([*check, str(restored_partition)], check=True, capture_output=True)
            self.assertLess(target.stat().st_blocks * 512, size // 2)


if __name__ == "__main__":
    unittest.main()
