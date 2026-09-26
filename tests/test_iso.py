import argparse
import gzip
import hashlib
import io
import json
import zipfile
import shutil
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.iso  # noqa: E402
import vmctl.runtime  # noqa: E402
from vmctl.errors import VMError  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class IsoTests(BaseVmctlTestCase):
    def test_ensure_iso_skips_download_when_file_exists(self):
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("already here", encoding="utf-8")

        with mock.patch.object(vmctl.iso, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        download_file.assert_not_called()

    def test_download_file_sets_user_agent_header(self):
        destination = self.root / "isos" / "download.iso"

        class FakeResponse:
            def __init__(self):
                self._chunks = [b"payload", b""]
                self.headers = {"Content-Type": "application/octet-stream"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size=-1):
                return self._chunks.pop(0)

        with mock.patch.object(urllib.request, "urlopen", return_value=FakeResponse()) as urlopen_mock:
            self.vmctl.download_file("https://example.invalid/test.iso", destination)

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/test.iso")
        self.assertEqual(request.headers["User-agent"], self.vmctl.HTTP_USER_AGENT)
        self.assertEqual(destination.read_bytes(), b"payload")

    def test_download_file_rejects_html_response(self):
        destination = self.root / "isos" / "fedora.iso"

        class FakeResponse:
            def __init__(self):
                self.headers = {"Content-Type": "text/html; charset=utf-8"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size=-1):
                return b"<!doctype html><html></html>"

        with mock.patch.object(urllib.request, "urlopen", return_value=FakeResponse()):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.download_file("https://example.invalid/fedora.iso", destination)

        self.assertFalse(destination.exists())

    def test_download_file_rejects_incomplete_response_and_keeps_existing_iso(self):
        destination = self.root / "isos" / "download.iso"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"old iso")

        class FakeResponse:
            def __init__(self):
                self._chunks = [b"partial", b""]
                self.headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": "1024",
                }

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size=-1):
                return self._chunks.pop(0)

        with mock.patch.object(urllib.request, "urlopen", return_value=FakeResponse()):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.download_file("https://example.invalid/test.iso", destination)

        self.assertEqual(destination.read_bytes(), b"old iso")
        self.assertFalse(destination.with_name(destination.name + ".part").exists())

    def test_ensure_iso_removes_invalid_cached_html_and_redownloads(self):
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("<!doctype html><html></html>", encoding="utf-8")

        with mock.patch.object(vmctl.iso, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=False, vm=self.vm_config)

    def test_ensure_iso_removes_cached_file_with_bad_size_and_redownloads(self):
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_bytes(b"partial")
        self.vm_config["iso_size"] = 1024

        with mock.patch.object(vmctl.iso, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        self.assertFalse(iso_path.exists())
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=False, vm=self.vm_config)

    def test_ensure_iso_uses_discovered_url_before_hardcoded_fallback(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["iso_discovery"] = {
            "index_url": "https://example.invalid/releases/",
            "pattern": r'href="(?P<url>test-[0-9]+\.iso)"',
        }

        with mock.patch.object(vmctl.iso, "fetch_text", return_value='<a href="test-2.iso">test-2.iso</a>'), \
             mock.patch.object(vmctl.iso, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        download_file.assert_called_once_with("https://example.invalid/releases/test-2.iso", iso_path, dry_run=False, vm=self.vm_config)

    def test_discovery_url_template_builds_iso_url_from_release_directories(self):
        self.vm_config["iso_discovery"] = {
            "index_url": "https://mirror.example.invalid/ISO/desktop/",
            "pattern": r'href="(\d{6})/"',
            "url_template": "https://mirror.example.invalid/ISO/desktop/{match}/example-{match}.iso",
            "sort": "desc",
            "limit": 1,
        }
        index = '<a href="../">..</a> <a href="260426/">260426/</a> <a href="260809/">260809/</a> <a href="260628/">260628/</a>'
        with mock.patch.object(vmctl.iso, "fetch_text", return_value=index):
            urls = self.vmctl.discover_iso_urls(self.vm_config)
        self.assertEqual(urls, ["https://mirror.example.invalid/ISO/desktop/260809/example-260809.iso"])

    def test_ensure_iso_dry_run_skips_remote_discovery(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["iso_discovery"] = {
            "index_url": "https://example.invalid/releases/",
            "pattern": r'href="(?P<url>test-[0-9]+\.iso)"',
        }

        with mock.patch.object(vmctl.iso, "fetch_text") as fetch_text, \
             mock.patch.object(vmctl.iso, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config, dry_run=True)

        self.assertEqual(resolved, iso_path)
        fetch_text.assert_not_called()
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)

    def test_ensure_iso_falls_back_to_hardcoded_url_when_discovered_url_fails(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["iso_discovery"] = {
            "index_url": "https://example.invalid/releases/",
            "pattern": r'href="(?P<url>test-[0-9]+\.iso)"',
        }

        def fail_first(url, destination, dry_run=False, vm=None):
            if url.endswith("test-2.iso"):
                raise self.vmctl.VMError("mirror failed")

        with mock.patch.object(vmctl.iso, "fetch_text", return_value='<a href="test-2.iso">test-2.iso</a>'), \
             mock.patch.object(vmctl.iso, "download_file", side_effect=fail_first) as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        self.assertEqual(download_file.call_args_list[0].args[:2], ("https://example.invalid/releases/test-2.iso", iso_path))
        self.assertEqual(download_file.call_args_list[1].args[:2], (self.vm_config["iso_url"], iso_path))

    def test_ensure_iso_falls_back_to_hardcoded_url_when_discovery_index_fails(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["iso_discovery"] = {
            "index_url": "https://example.invalid/releases/",
            "pattern": r'href="(?P<url>test-[0-9]+\.iso)"',
        }

        with mock.patch.object(vmctl.iso, "fetch_text", side_effect=self.vmctl.VMError("index failed")), \
             mock.patch.object(vmctl.iso, "download_file") as download_file:
            resolved = self.vmctl.ensure_iso(self.vm_config)

        self.assertEqual(resolved, iso_path)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=False, vm=self.vm_config)

    def test_cmd_prep_fails_without_iso_url(self):
        self.vm_config.pop("iso_url")
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(vmctl.runtime, "require_command"):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_prep(args)

    def test_missing_local_only_iso_includes_profile_notes(self):
        self.vm_config.pop("iso_url")
        self.vm_config["notes"] = "Put this vendor ISO under isos/example.iso."

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.ensure_iso(self.vm_config)

        message = str(ctx.exception)
        self.assertIn("needs an ISO that vmctl cannot download", message)
        self.assertIn("Profile notes: Put this vendor ISO under isos/example.iso.", message)


class ArchIsoBootArtifactTests(BaseVmctlTestCase):
    def _extract(self) -> list[list[str]]:
        iso_path = self.root / "isos" / "live.iso"
        with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/xorriso" if name == "xorriso" else None), \
             mock.patch.object(vmctl.runtime, "run") as run:
            kernel, initrd = vmctl.iso.extract_arch_installer_boot_artifacts(self.vm_config, iso_path)
        self.assertEqual(kernel, self.root / "artifacts/testvm/installer/vmlinuz")
        self.assertEqual(initrd, self.root / "artifacts/testvm/installer/initrd")
        return [call.args[0] for call in run.call_args_list]

    def test_extract_arch_boot_artifacts_defaults_to_arch_linux_paths(self):
        commands = self._extract()
        self.assertIn("/arch/boot/x86_64/vmlinuz-linux", commands[0])
        self.assertIn("/arch/boot/x86_64/initramfs-linux.img", commands[1])

    def test_extract_arch_boot_artifacts_honors_installer_boot_for_derivatives(self):
        self.vm_config["installer_boot"] = {
            "kernel": "arch/boot/x86_64/vmlinuz-linux-cachyos",
            "initrd": "arch/boot/x86_64/initramfs-linux-cachyos.img",
        }
        commands = self._extract()
        self.assertIn("/arch/boot/x86_64/vmlinuz-linux-cachyos", commands[0])
        self.assertIn("/arch/boot/x86_64/initramfs-linux-cachyos.img", commands[1])


if __name__ == "__main__":
    unittest.main()


class IsoArchiveTests(BaseVmctlTestCase):
    """ISOs shipped inside an archive (ReactOS zip, pfSense .iso.gz) and ISOs only the user has."""

    PAYLOAD = b"CD001" + b"\0" * 4091

    def vm(self, **extra):
        vm = {"name": "Test OS", "iso": "isos/test.iso", "iso_url": "https://example.invalid/test.bin",
              "iso_sha256": hashlib.sha256(self.PAYLOAD).hexdigest(), "disk": {"path": "artifacts/t/disk.qcow2"}}
        vm.update(extra)
        return vm

    def fetch_writing(self, data):
        return lambda url, destination: destination.write_bytes(data)

    def zip_bytes(self, member="test.iso"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr(member, self.PAYLOAD)
        return buffer.getvalue()

    def test_zip_member_is_extracted_verified_and_the_archive_removed(self):
        vm = self.vm(iso_archive={"type": "zip", "member": "test.iso"})
        with mock.patch.object(vmctl.iso, "_fetch", side_effect=self.fetch_writing(self.zip_bytes())), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            path = vmctl.iso.ensure_iso(vm)
        self.assertEqual(path.read_bytes(), self.PAYLOAD)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["test.iso"])

    def test_gzip_archive_hash_is_checked_before_extracting(self):
        packed = gzip.compress(self.PAYLOAD)
        good = self.vm(iso_archive={"type": "gzip", "sha256": hashlib.sha256(packed).hexdigest()})
        with mock.patch.object(vmctl.iso, "_fetch", side_effect=self.fetch_writing(packed)), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(vmctl.iso.ensure_iso(good).read_bytes(), self.PAYLOAD)
        (self.root / "isos/test.iso").unlink()
        bad = self.vm(iso_archive={"type": "gzip", "sha256": "0" * 64})
        with mock.patch.object(vmctl.iso, "_fetch", side_effect=self.fetch_writing(packed)), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaisesRegex(VMError, "does not match iso_archive.sha256"):
                vmctl.iso.ensure_iso(bad)
        self.assertEqual(list((self.root / "isos").iterdir()), [])

    def test_a_missing_member_or_a_wrong_iso_leaves_nothing_behind(self):
        for vm, data, message in (
                (self.vm(iso_archive={"type": "zip", "member": "other.iso"}), self.zip_bytes(), "not in the archive"),
                (self.vm(iso_archive={"type": "zip", "member": "test.iso"}, iso_sha256="0" * 64), self.zip_bytes(), "Invalid ISO extracted")):
            with self.subTest(message=message), \
                 mock.patch.object(vmctl.iso, "_fetch", side_effect=self.fetch_writing(data)), \
                 mock.patch("sys.stdout", new_callable=io.StringIO):
                with self.assertRaisesRegex(VMError, message):
                    vmctl.iso.ensure_iso(vm)
                self.assertEqual(list((self.root / "isos").iterdir()), [])

    def test_source_kind_and_the_message_for_an_iso_only_the_user_has(self):
        manual = {"name": "Windows XP", "iso": "isos/xp.iso", "iso_help": "Use your own CD and its key."}
        self.assertEqual(vmctl.iso.iso_source_kind(manual), "manual")
        self.assertEqual(vmctl.iso.iso_source_kind(self.vm()), "download")
        with self.assertRaises(VMError) as raised:
            vmctl.iso.ensure_iso(manual)
        text = str(raised.exception)
        self.assertIn("cannot download: isos/xp.iso", text)
        self.assertIn("Use your own CD and its key.", text)
        self.assertIn('set "iso"', text)
        (self.root / "isos").mkdir(exist_ok=True)
        (self.root / "isos/xp.iso").write_bytes(self.PAYLOAD)
        self.assertEqual(vmctl.iso.iso_source_kind(manual), "cached")

    def test_every_tracked_profile_can_get_its_iso_or_says_how(self):
        # The rule this commit set: vmctl downloads what has a public source, and a medium only
        # the user can provide carries iso_help (Windows, the retro media, pearOS's signed links).
        for path in (ROOT / "vms/profiles").glob("*.json"):
            if path.name.startswith("local"):
                continue
            for name, vm in json.loads(path.read_text())["vms"].items():
                with self.subTest(profile=name):
                    has_source = vm.get("iso_url") or vm.get("iso_urls") or vm.get("iso_discovery")
                    self.assertTrue(has_source or vm.get("iso_help"), f"{name}: no download source and no iso_help")


class DownloadProgressTests(unittest.TestCase):
    """A job log (the web page, the TUI) has no terminal: the download says how far it is anyway."""

    def test_a_job_log_gets_a_line_every_ten_percent(self):
        import contextlib
        import io

        from vmctl import iso

        data = b"x" * (10 * 1024 * 1024)
        out, target = io.StringIO(), io.BytesIO()
        with contextlib.redirect_stdout(out):
            iso.copy_with_progress(io.BytesIO(data), target, len(data))
        self.assertEqual(target.getvalue(), data)
        lines = out.getvalue().splitlines()
        self.assertEqual([line.split()[1] for line in lines], [f"{n}%" for n in range(10, 101, 10)])
        self.assertNotIn("\r", out.getvalue())
