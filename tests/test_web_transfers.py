"""Host and guest copies through isolated OpenSSH SFTP processes, never real VMs."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vmctl import web_files, web_transfers
from vmctl.errors import VMError
from tests.test_web_files import SFTP_SERVER


@unittest.skipUnless(SFTP_SERVER, "OpenSSH sftp-server is not installed")
class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("source", "target"):
            (self.root / name).mkdir()
        original = web_files.SFTP

        def client(vm):
            with mock.patch.object(web_files.ssh, "ssh_base_cmd", return_value=["ssh", "fixture"]):
                result = original(vm, timeout=5)
            result.command = [SFTP_SERVER, "-d", str(self.root / vm["name"])]
            return result

        patch = mock.patch.object(web_files, "SFTP", side_effect=client)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(web_transfers.config, "get_vm", side_effect=lambda cfg, name: {"name": name})
        patch.start()
        self.addCleanup(patch.stop)
        self.manager = web_transfers.Transfers(limit=2)
        self.addCleanup(self.manager.close_all)
        self.client_type = original

    def host(self, name="test.bin", data=b"hello"):
        info = self.manager.create({"vm": "target", "name": name, "size": len(data)}, {})
        transfer = self.manager.get(info["id"])
        transfer.receive(io.BytesIO(data), len(data))
        return transfer

    def guest(self, path="test.bin"):
        info = self.manager.create({"vm": "target", "source_vm": "source", "source_path": path}, {})
        return self.manager.get(info["id"])

    def finished(self, transfer):
        if transfer.thread:
            transfer.thread.join(10)
            self.assertFalse(transfer.thread.is_alive())
        self.assertIn(transfer.info()["status"], web_transfers.FINISHED)
        self.assertFalse(list((self.root / "target").glob(".vmctl-upload-*")))
        return transfer.info()

    def test_host_binary_empty_and_duplicate_copies(self):
        payload = bytes(range(256)) * 400
        for data in (payload, b""):
            result = self.finished(self.host(data=data))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(Path(result["result"]["path"]).read_bytes(), data)
        self.assertEqual((self.root / "target/test.bin").read_bytes(), payload)
        self.assertTrue((self.root / "target/test (2).bin").exists())

    def test_guest_copy_uses_actual_source_size_and_preserves_source_and_target(self):
        name = 'café $(touch pwned).bin'
        payload = bytes(range(256)) * 300
        (self.root / "source" / name).write_bytes(payload)
        (self.root / "target" / name).write_bytes(b"keep")
        result = self.finished(self.guest(name))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["size"], len(payload))
        self.assertEqual(Path(result["result"]["path"]).read_bytes(), payload)
        self.assertEqual((self.root / "source" / name).read_bytes(), payload)
        self.assertEqual((self.root / "target" / name).read_bytes(), b"keep")

    def test_guest_copy_rejects_symlinks_directories_and_missing_files(self):
        (self.root / "source/real").touch()
        (self.root / "source/link").symlink_to("real")
        (self.root / "source/folder").mkdir()
        for name in ("link", "folder", "missing"):
            with self.subTest(name=name):
                self.assertEqual(self.finished(self.guest(name))["status"], "failed")
        self.assertFalse(list((self.root / "target").iterdir()))

    def test_waiting_cancel_limits_validation_and_interrupted_receive(self):
        body = {"vm": "target", "name": "x", "size": 20}
        first = self.manager.get(self.manager.create(body, {})["id"])
        second = self.manager.get(self.manager.create(body, {})["id"])
        with self.assertRaisesRegex(VMError, "Too many"):
            self.manager.create(body, {})
        self.assertEqual(first.cancel()["status"], "cancelled")
        with self.assertRaisesRegex(VMError, "not waiting"):
            first.receive(io.BytesIO(b"x"), 20)
        with self.assertRaisesRegex(VMError, "does not match"):
            second.receive(io.BytesIO(b"x"), 1)
        with self.assertRaisesRegex(VMError, "interrupted"):
            second.receive(io.BytesIO(b"x"), 20)
        self.assertEqual(second.info()["status"], "failed")
        for change in ({"size": web_files.MAX_FILE_SIZE + 1}, {"size": -1}, {"size": True}, {"name": "../bad"}, {"path": "\0"}):
            with self.subTest(change=change), self.assertRaises(VMError):
                self.manager.create(body | change, {})

    def test_cancelling_during_guest_write_cleans_partial_without_replacing(self):
        original = self.client_type.upload
        (self.root / "target/test.bin").write_bytes(b"original")

        def upload(client, directory, name, source, size, progress):
            def update(count):
                if count:
                    # The bound callback belongs to the transfer currently being copied.
                    progress.__self__.cancel()
                progress(count)
            return original(client, directory, name, source, size, update)

        with mock.patch.object(self.client_type, "upload", upload):
            result = self.finished(self.host(data=b"x" * 100000))
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual((self.root / "target/test.bin").read_bytes(), b"original")
        self.assertEqual(len(list((self.root / "target").iterdir())), 1)

    def test_cancelling_during_source_read_never_writes_destination(self):
        original = self.client_type.download
        (self.root / "source/test.bin").write_bytes(b"x" * 100000)

        def download(client, path, target, progress):
            def update(count):
                if count:
                    progress.__self__.cancel()
                progress(count)
            return original(client, path, target, update)

        with mock.patch.object(self.client_type, "download", download):
            self.assertEqual(self.finished(self.guest())["status"], "cancelled")
        self.assertFalse(list((self.root / "target").iterdir()))

    def test_cancel_racing_completed_rename_reports_completed(self):
        original = self.client_type.upload

        def upload(client, directory, name, source, size, progress):
            result = original(client, directory, name, source, size, progress)
            progress.__self__.cancel()
            return result

        with mock.patch.object(self.client_type, "upload", upload):
            transfer = self.host()
            self.assertEqual(self.finished(transfer)["status"], "completed")
        self.assertEqual(transfer.cancel()["status"], "completed")

    def test_staging_failure_releases_capacity_and_reports_error(self):
        info = self.manager.create({"vm": "target", "name": "x", "size": 1}, {})
        transfer = self.manager.get(info["id"])
        with mock.patch.object(web_transfers.tempfile, "TemporaryFile", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                transfer.receive(io.BytesIO(b"x"), 1)
        self.assertEqual(transfer.info()["status"], "failed")
        self.assertIn("disk full", transfer.info()["error"])

    def test_transfer_history_is_bounded_and_expired_waiting_jobs_release_capacity(self):
        body = {"vm": "target", "name": "x", "size": 1}
        transfer = self.manager.get(self.manager.create(body, {})["id"])
        transfer.updated -= 301
        for _ in range(105):
            self.manager.get(self.manager.create(body, {})["id"]).cancel()
        self.assertEqual(transfer.info()["status"], "cancelled")
        self.assertEqual(len(self.manager.entries), 100)
