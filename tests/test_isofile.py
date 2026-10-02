import argparse
import io
import json
import struct
from pathlib import Path
from unittest import mock

from tests._common import BaseVmctlTestCase
from vmctl import config, isofile, lifecycle
from vmctl.errors import VMError


def fake_wim(images, padding=4096):
    """A WIM with the real header layout: the XML resource header at 72 points past *padding*."""
    body = "<WIM>" + "".join(
        f"<IMAGE INDEX='{i}'><NAME>{name}</NAME><WINDOWS><LANGUAGES>"
        + "".join(f"<LANGUAGE>{lang}</LANGUAGE>" for lang in langs)
        + "</LANGUAGES></WINDOWS></IMAGE>"
        for i, (name, langs) in enumerate(images, 1)) + "</WIM>"
    xml = "﻿".encode("utf-16-le") + body.encode("utf-16-le")
    header = bytearray(isofile.WIM_HEADER_SIZE)
    header[:8] = isofile.WIM_MAGIC
    offset = isofile.WIM_HEADER_SIZE + padding
    header[72:79] = len(xml).to_bytes(7, "little")
    struct.pack_into("<Q", header, 80, offset)
    return bytes(header) + b"\0" * padding + xml


def fake_iso(path: Path, size=64 * 1024):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(size)
    data[16 * 2048 + 1:16 * 2048 + 6] = b"CD001"
    path.write_bytes(bytes(data))
    return path


class WimTests(BaseVmctlTestCase):
    def test_image_names_and_languages_are_read_from_a_stream(self):
        images = isofile.wim_images(io.BytesIO(fake_wim([("Windows 11 Home", ["it-IT"]), ("Windows 11 Pro", ["it-IT"])])))
        self.assertEqual(images, [{"name": "Windows 11 Home", "languages": ["it-IT"]},
                                  {"name": "Windows 11 Pro", "languages": ["it-IT"]}])
        with self.assertRaisesRegex(VMError, "MSWIM"):
            isofile.wim_images(io.BytesIO(b"not a wim" * 50))
        with self.assertRaisesRegex(VMError, "ends"):
            isofile.wim_images(io.BytesIO(fake_wim([("X", [])])[:300]))

    def test_mismatch_follows_a_single_language_and_refuses_a_missing_edition(self):
        italian = [{"name": "Windows 11 Pro", "languages": ["it-IT"]}]
        # 2026-10-02: the catalog's en-US against an Italian ISO parked Setup on its first page.
        self.assertEqual(isofile.windows_mismatch({"edition": "Windows 11 Pro"}, italian), ([], "it-IT"))
        self.assertEqual(isofile.windows_mismatch({"edition": "Windows 11 Pro", "language": "it-IT"}, italian), ([], None))
        problems, _ = isofile.windows_mismatch({"edition": "Windows 11 Enterprise", "language": "it-IT"}, italian)
        self.assertIn("no image named 'Windows 11 Enterprise'", problems[0])
        self.assertEqual(isofile.windows_mismatch({"edition": "Whatever", "image_index": 3, "language": "it-IT"}, italian), ([], None))
        multi = [{"name": "Windows 11 Pro", "languages": ["de-DE", "fr-FR"]}]
        problems, use = isofile.windows_mismatch({"edition": "Windows 11 Pro", "language": "it-IT"}, multi)
        self.assertIsNone(use)
        self.assertIn("the ISO has de-DE, fr-FR", problems[0])

    def test_reconcile_switches_the_language_before_setup_and_fails_fast_on_the_edition(self):
        iso_path = fake_iso(self.root / "isos/win.iso")
        vm = {"windows_config": {"edition": "Windows 11 Pro", "language": "en-US", "input_locale": "it-IT"}}
        with mock.patch.object(isofile, "windows_images", return_value=[{"name": "Windows 11 Pro", "languages": ["it-IT"]}]):
            fixed = isofile.reconcile_windows_medium("win", vm, iso_path)
            self.assertEqual(fixed["windows_config"]["language"], "it-IT")
            self.assertEqual(fixed["windows_config"]["input_locale"], "it-IT")
            self.assertEqual(vm["windows_config"]["language"], "en-US")  # the profile itself is untouched
            self.assertIs(isofile.reconcile_windows_medium("win", vm, iso_path, dry_run=True), vm)
            with self.assertRaisesRegex(VMError, "vmctl iso set win"):
                isofile.reconcile_windows_medium("win", {"windows_config": {"edition": "Windows 11 Home"}}, iso_path)
        with mock.patch.object(isofile, "windows_images", side_effect=VMError("Missing 7z")):
            self.assertIs(isofile.reconcile_windows_medium("win", vm, iso_path), vm)  # warns, flow unchanged

    def test_the_image_list_is_cached_per_medium(self):
        iso_path = fake_iso(self.root / "isos/win.iso")
        with mock.patch.object(isofile, "_read_windows_images", return_value=[{"name": "A", "languages": []}]) as read:
            isofile.windows_images(iso_path)
            isofile.windows_images(iso_path)
            self.assertEqual(read.call_count, 1)
            iso_path.write_bytes(iso_path.read_bytes() + b"\0" * 2048)  # another medium under the same name
            isofile.windows_images(iso_path)
            self.assertEqual(read.call_count, 2)


class IsoFileTests(BaseVmctlTestCase):
    def local(self):
        path = self.config_dir / "profiles" / "local.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def test_browse_lists_directories_and_isos_only(self):
        folder = self.root / "downloads"
        (folder / "sub").mkdir(parents=True)
        (folder / ".hidden").mkdir()
        fake_iso(folder / "b.iso")
        (folder / "notes.txt").write_text("x")
        with mock.patch.object(isofile, "places", return_value=[]):
            listing = isofile.browse(str(folder))
        self.assertEqual([(e["name"], e["kind"]) for e in listing["entries"]], [("sub", "dir"), ("b.iso", "iso")])
        self.assertEqual(listing["parent"], str(self.root))
        with self.assertRaisesRegex(VMError, "absolute"):
            isofile.browse("relative/dir")

    def test_check_refuses_a_file_that_is_not_an_iso(self):
        html = self.root / "dl/page.iso"
        html.parent.mkdir(parents=True)
        html.write_text("<!doctype html><html></html>")
        self.assertTrue(any("HTML" in p for p in isofile.check(self.vm_name, html)["problems"]))
        blob = self.root / "dl/blob.iso"
        blob.write_bytes(b"\1" * 40000)
        self.assertIn("no ISO 9660 volume descriptor", isofile.check(self.vm_name, blob)["problems"][0])
        self.assertEqual(isofile.check(self.vm_name, fake_iso(self.root / "dl/ok.iso"))["problems"], [])

    def test_set_keeps_the_file_where_it_is_through_local_json(self):
        source = fake_iso(self.root / "library/Win11.iso")
        isofile.set_iso(self.vm_name, source)
        self.assertEqual(self.local()["vms"][self.vm_name]["iso"], str(source))
        self.assertTrue(source.exists())
        self.assertEqual(config.get_vm(config.load_config(), self.vm_name)["iso"], str(source))

    def test_set_move_renames_into_isos_and_drops_the_override(self):
        source = fake_iso(self.root / "library/Win11.iso")
        isofile.set_iso(self.vm_name, source)  # first used in place...
        isofile.set_iso(self.vm_name, source, move=True)  # ...then moved under the catalog's name
        target = self.root / "isos/test.iso"
        self.assertTrue(target.is_file())
        self.assertFalse(source.exists())
        self.assertNotIn(self.vm_name, self.local().get("vms", {}))
        other = fake_iso(self.root / "library/Other.iso")
        with self.assertRaisesRegex(VMError, "already exists"):
            isofile.set_iso(self.vm_name, other, move=True)
        self.assertTrue(other.exists())

    def test_set_refuses_a_bad_file_unless_forced(self):
        blob = self.root / "library/blob.iso"
        blob.parent.mkdir(parents=True)
        blob.write_bytes(b"\1" * 40000)
        with self.assertRaisesRegex(VMError, "--force"):
            isofile.set_iso(self.vm_name, blob)
        self.assertEqual(self.local(), {})
        isofile.set_iso(self.vm_name, blob, force=True)
        self.assertEqual(self.local()["vms"][self.vm_name]["iso"], str(blob))

    def test_delete_iso_removes_only_files_under_isos(self):
        source = fake_iso(self.root / "library/Win11.iso")
        isofile.set_iso(self.vm_name, source)
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)
        with self.assertRaisesRegex(VMError, "not in vmctl's ISO cache"):
            lifecycle.cmd_delete_iso(args)
        self.assertTrue(source.exists())
        isofile.set_iso(self.vm_name, source, move=True)
        lifecycle.cmd_delete_iso(args)
        self.assertFalse((self.root / "isos/test.iso").exists())
