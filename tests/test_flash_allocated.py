import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest import mock

from vmctl import flash_allocated as allocated, import_allocated, runtime
from vmctl.errors import VMError


class AllocatedFlashTests(unittest.TestCase):
    def test_partition_geometry_rejects_unsupported_and_invalid_layouts(self):
        valid = {"label": "gpt", "unit": "sectors", "sectorsize": 512,
                 "partitions": [{"start": 2, "size": 4, "type": "83"}]}
        for changes in ({"label": "other"}, {"sectorsize": 4096}, {"partitions": []},
                        {"partitions": [{"start": 0, "size": 4}]},
                        {"partitions": [{"start": 2, "size": 9}]},
                        {"partitions": [{"start": 2, "size": 4}, {"start": 3, "size": 4}]},
                        {"label": "dos", "partitions": [{"start": 2, "size": 4, "type": "0f"}]}):
            with self.subTest(changes=changes), mock.patch.object(runtime, "run_output", return_value=json.dumps({"partitiontable": dict(valid, **changes)})):
                with self.assertRaises(VMError):
                    allocated.partition_geometry(Path("source"), 4096)

    def test_partition_view_is_read_only_and_detached_on_failure(self):
        with mock.patch.object(runtime, "run_output", return_value="/dev/loop123\n") as attach, \
             mock.patch.object(runtime, "run") as detach:
            with self.assertRaisesRegex(VMError, "scan failed"):
                with allocated.partition_view(Path("source"), 1024, 4096):
                    raise VMError("scan failed")
            self.assertEqual(attach.call_args.args[0], ["losetup", "--find", "--show", "--read-only",
                             "--offset", "1024", "--sizelimit", "4096", "--sector-size", "512", "source"])
            detach.assert_called_once_with(["losetup", "--detach", "/dev/loop123"], quiet=True)

    def test_unknown_and_encrypted_filesystems_are_copied_in_full(self):
        for fstype in ("crypto_LUKS", "BitLocker", "", "xfs"):
            with self.subTest(fstype=fstype), \
                 mock.patch.object(allocated, "partition_geometry", return_value=[(512, 1024)]), \
                 mock.patch.object(allocated, "partition_view", return_value=nullcontext("/dev/loop123")), \
                 mock.patch.object(runtime, "run_output", return_value=fstype), \
                 mock.patch.object(runtime, "run") as run:
                blocks = allocated.build_domain(Path("source"), 2048, Path("scratch"))
                self.assertEqual(blocks, [import_allocated.Block(0, 2048, "+")])
                run.assert_not_called()

    def test_probe_errors_do_not_silently_fall_back_to_a_full_copy(self):
        with mock.patch.object(allocated, "partition_geometry", return_value=[(512, 1024)]), \
             mock.patch.object(allocated, "partition_view", return_value=nullcontext("/dev/loop123")), \
             mock.patch.object(runtime, "run_output", side_effect=subprocess.CalledProcessError(4, "blkid")):
            with self.assertRaises(subprocess.CalledProcessError):
                allocated.build_domain(Path("source"), 2048, Path("scratch"))

    def test_running_vm_and_4k_target_rejected_before_staging(self):
        for pid, sector in ((123, 512), (None, 4096)):
            with self.subTest(pid=pid, sector=sector), \
                 mock.patch.object(allocated.lifecycle, "running_qemu_pid", return_value=pid), \
                 mock.patch.object(runtime, "run") as run:
                with self.assertRaises(VMError):
                    with allocated.prepare("vm", {}, Path("source"), {"logical_sector_size": sector}):
                        self.fail("unsafe source or target accepted")
                run.assert_not_called()

    def test_copy_checks_completion_and_never_uses_sparse_writes(self):
        prepared = allocated.PreparedCopy(Path("source"), Path("domain"), Path("rescue"), 1024)
        with mock.patch.object(runtime, "run", side_effect=[None, subprocess.CalledProcessError(1, "ddrescuelog")]) as run, \
             mock.patch.object(import_allocated, "read_map", return_value=[import_allocated.Block(0, 1024, "-")]):
            with self.assertRaisesRegex(VMError, "Flash incomplete"):
                prepared.copy_to("/dev/fake")
            cmd = run.call_args_list[0].args[0]
            self.assertIn("--force", cmd)
            self.assertNotIn("--sparse", cmd)
            self.assertNotIn("--truncate", cmd)

    def test_short_rescue_map_is_rejected(self):
        prepared = allocated.PreparedCopy(Path("source"), Path("domain"), Path("rescue"), 1024)
        with mock.patch.object(runtime, "run"), \
             mock.patch.object(import_allocated, "read_map", return_value=[import_allocated.Block(0, 512, "+")]):
            with self.assertRaisesRegex(VMError, "does not cover"):
                prepared.copy_to("/dev/fake")

    def test_scan_failure_cleans_staging_and_reports_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.qcow2"
            source.touch()
            def convert(cmd, **kwargs):
                Path(cmd[-1]).write_bytes(bytes(4096))
            def scan(raw, size, work):
                (work / "partition-0.log").write_text("NTFS is hibernated")
                raise VMError("partclone rejected source")
            with mock.patch.object(allocated.lifecycle, "running_qemu_pid", return_value=None), \
                 mock.patch.object(runtime, "run", side_effect=convert), \
                 mock.patch.object(allocated, "build_domain", side_effect=scan):
                with self.assertRaisesRegex(VMError, "NTFS is hibernated"):
                    with allocated.prepare("vm", {"disk": {"format": "qcow2"}}, source, {"size": 8192}):
                        self.fail("scan failure accepted")
            self.assertEqual(list(Path(directory).iterdir()), [source])


@unittest.skipUnless(all(shutil.which(tool) for tool in ("qemu-img", "sfdisk", "blkid", "ddrescue", "ddrescuelog")),
                     "real flash tools are not installed")
class AllocatedFlashIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(all(shutil.which(tool) for tool in ("mkfs.ext4", "partclone.extfs", "e2fsck")), "ext4 tools unavailable")
    def test_ext4_copy_preserves_allocated_zeros_on_dirty_target(self):
        self.roundtrip("ext4", ["e2fsck", "-f", "-n"])

    @unittest.skipUnless(all(shutil.which(tool) for tool in ("mkntfs", "partclone.ntfs", "ntfscp", "ntfsfix", "ntfscat")), "NTFS tools unavailable")
    def test_ntfs_copy_preserves_zero_file_on_dirty_target(self):
        self.roundtrip("ntfs", ["ntfsfix", "-n"])

    def roundtrip(self, fstype, check):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partition = root / "partition.raw"
            contents = root / "files"
            contents.mkdir()
            payload = b"A" * 65536 + bytes(65536) + b"B" * 65536
            (contents / "payload").write_bytes(payload)
            with partition.open("wb") as output:
                output.truncate(32 * 1024**2)
            if fstype == "ext4":
                subprocess.run(["mkfs.ext4", "-q", "-F", "-d", str(contents), str(partition)], check=True, capture_output=True)
            else:
                subprocess.run(["mkntfs", "-F", "-Q", "-s", "512", str(partition)], check=True, capture_output=True)
                subprocess.run(["ntfscp", str(partition), str(contents / "payload"), "/payload"], check=True, capture_output=True)
            prefix = 1024**2
            size = partition.stat().st_size + 4 * prefix
            raw = root / "source.raw"
            with raw.open("wb") as output:
                output.truncate(size)
            subprocess.run(["sfdisk", str(raw)], input="label: gpt\nstart=2048, size=65536\n", text=True, check=True, capture_output=True)
            with raw.open("r+b") as output:
                output.seek(prefix)
                output.write(partition.read_bytes())
            image, target = root / "disk.qcow2", root / "target.raw"
            subprocess.run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(raw), str(image)], check=True)
            target.write_bytes(b"X" * size)
            # Real QEMU reproducer: zero detection loses guest data on a used disk.
            subprocess.run(["qemu-img", "convert", "-n", "--target-is-zero", "-f", "qcow2", "-O", "raw", str(image), str(target)], check=True)
            self.assertNotEqual(target.read_bytes(), raw.read_bytes())
            target.write_bytes(b"X" * (size + prefix))

            @contextmanager
            def file_view(staged, start, length):
                # Only kernel loop attachment is substituted; all image, bitmap,
                # copy and verification tools operate on real temporary files.
                view = root / "view.raw"
                with staged.open("rb") as source:
                    source.seek(start)
                    view.write_bytes(source.read(length))
                yield str(view)

            vm = {"disk": {"format": "qcow2"}}
            with mock.patch.object(allocated.lifecycle, "running_qemu_pid", return_value=None), \
                 mock.patch.object(allocated, "partition_view", side_effect=file_view):
                with allocated.prepare("test", vm, image, {"size": size + prefix}) as prepared:
                    blocks = import_allocated.read_map(prepared.domain, size, "?+")
                    self.assertGreater(sum(block.size for block in blocks if block.status == "?"), 16 * prefix)
                    prepared.copy_to(str(target))
                    data, original = target.read_bytes(), raw.read_bytes()
                    for block in blocks:
                        expected = original[block.start:block.end] if block.status == "+" else b"X" * block.size
                        self.assertEqual(data[block.start:block.end], expected)
                    self.assertEqual(data[size:], b"X" * prefix)
            self.assertFalse(list(root.glob(".allocated-flash-*")))
            restored = root / "restored.partition"
            restored.write_bytes(data[prefix:prefix + partition.stat().st_size])
            result = subprocess.run([*check, str(restored)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if fstype == "ntfs":
                result = subprocess.run(["ntfscat", str(restored), "/payload"], check=True, capture_output=True)
                self.assertEqual(result.stdout, payload)


if __name__ == "__main__":
    unittest.main()
