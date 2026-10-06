"""vmctl usage: the checkout's disk usage by section, allocated blocks, nothing followed."""
import argparse
import io
import json
import os
from contextlib import redirect_stdout

from tests._common import BaseVmctlTestCase
from vmctl import usage


class UsageTests(BaseVmctlTestCase):
    def fill(self, path, size):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\1" * size)
        return path

    def test_sections_and_vms_count_allocated_bytes_only(self):
        root = self.root
        self.fill(root / "isos" / "a.iso", 300_000)
        self.fill(root / "artifacts" / self.vm_name / "disk.qcow2", 200_000)
        self.fill(root / "artifacts" / self.vm_name / "md1.qcow2", 50_000)  # an extra disk
        self.fill(root / "artifacts" / self.vm_name / "checkpoints" / "clean" / "disk.qcow2", 120_000)
        self.fill(root / "artifacts" / self.vm_name / "logs" / "serial.log", 10_000)
        self.fill(root / "artifacts" / self.vm_name / "recording" / "x" / "frames.ffconcat", 7_000)
        self.fill(root / "artifacts" / "check-vms" / "r1" / "results" / "a.json", 40_000)
        self.fill(root / "artifacts" / "tts" / "model.bin", 90_000)
        self.fill(root / "artifacts" / ".web-recordings" / "r" / "f.png", 5_000)
        self.fill(root / "artifacts" / "labs" / "links" / "session.json", 1_000)
        self.fill(root / "docs" / "media" / "clip.gif", 30_000)
        self.fill(root / "docs" / "WEB.md", 2_000)
        # A sparse image is capacity on paper only: it must count almost nothing.
        sparse = root / "artifacts" / "other-vm" / "disk.raw"
        sparse.parent.mkdir(parents=True)
        with sparse.open("wb") as handle:
            handle.truncate(500_000_000)
        link = root / "artifacts" / "shortcut"
        os.symlink(root / "isos", link)  # never followed: the ISOs would count twice

        data = usage.collect()
        sections = data["sections"]
        tolerance = 8192  # directory entries and block rounding
        self.assertAlmostEqual(sections["isos"], 300_000, delta=tolerance)
        self.assertAlmostEqual(sections["disks"], 250_000, delta=tolerance)
        self.assertAlmostEqual(sections["checkpoints"], 120_000, delta=tolerance)
        self.assertAlmostEqual(sections["vm_other"], 10_000, delta=tolerance * 3)
        self.assertAlmostEqual(sections["recordings"], 12_000, delta=tolerance * 2)
        self.assertAlmostEqual(sections["reports"], 40_000, delta=tolerance * 2)
        self.assertAlmostEqual(sections["media_tools"], 90_000, delta=tolerance)
        self.assertAlmostEqual(sections["labs"], 1_000, delta=tolerance)
        self.assertAlmostEqual(sections["docs_media"], 30_000, delta=tolerance)
        self.assertLess(sections["artifacts_other"], tolerance)  # the symlink alone
        self.assertEqual(sum(sections.values()), data["total"])
        names = [vm["name"] for vm in data["vms"]]
        self.assertEqual(names[0], self.vm_name)
        self.assertIn("other-vm", names)  # recognised by its disk, not by the catalog
        self.assertLess(next(vm for vm in data["vms"] if vm["name"] == "other-vm")["disks"], tolerance)

    def test_the_table_and_the_json(self):
        self.fill(self.root / "artifacts" / self.vm_name / "disk.qcow2", 1_500_000)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(usage.cmd_usage(argparse.Namespace(top=10, json=False)), 0)
        text = out.getvalue()
        self.assertIn("VM disks", text)
        self.assertIn("1.4 MB", text)
        self.assertIn(self.vm_name, text)
        self.assertIn("total", text)
        out = io.StringIO()
        with redirect_stdout(out):
            usage.cmd_usage(argparse.Namespace(top=0, json=True))
        self.assertEqual(json.loads(out.getvalue())["vms"][0]["name"], self.vm_name)

    def test_human_sizes(self):
        self.assertEqual(usage.human(0), "0 B")
        self.assertEqual(usage.human(1536), "1.5 KB")
        self.assertEqual(usage.human(3 * 1024 ** 3), "3.0 GB")
