import argparse
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


if __name__ == "__main__":
    unittest.main()
