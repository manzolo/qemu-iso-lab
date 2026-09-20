import unittest

from vmctl import tui_devices


class DeviceTableTests(unittest.TestCase):
    def setUp(self):
        self.disk = {
            "path": "/dev/sda", "size": 250059350016,
            "model": "Samsung SSD 850 EVO 250GB", "serial": "S2R6NX0H470302D",
            "tran": "sata", "mountpoints": [], "unallocated_bytes": 207110742528,
            "children": [
                {"path": "/dev/sda1", "size": 512 * 1024**2, "fstype": "vfat", "label": "EFI"},
                {"path": "/dev/sda2", "size": 40 * 1024**3, "fstype": "ext4", "label": "ROOT"},
            ],
        }

    def test_table_fits_terminal_and_keeps_serial_suffix_and_headers_aligned(self):
        for columns in (80, 100, 115, 160):
            with self.subTest(columns=columns):
                path, description = tui_devices.menu_items([self.disk], columns)[0]
                row, details = description.split("\t")
                header = tui_devices.menu_header(columns)
                self.assertEqual(path, "/dev/sda")
                self.assertLessEqual(len(row), columns - 8)
                self.assertEqual([i for i, char in enumerate(row) if char == "│"],
                                 [i for i, char in enumerate(header) if char == "│"])
                self.assertIn("H470302D", row)
                self.assertNotIn("sda2", row)
                self.assertIn(self.disk["serial"], details)
                self.assertIn(self.disk["model"], details)

    def test_preview_preserves_long_labels_mounts_and_nested_filesystems(self):
        self.disk["children"][1]["children"] = [
            {"path": "/dev/mapper/very-long-logical-volume-name", "size": 30 * 1024**3,
             "fstype": "ext4", "label": "A very long filesystem label that exceeds the table"}]
        self.disk["mountpoints"] = ["/media/backup"]
        preview = tui_devices.device_details(self.disk, 80)
        self.assertIn("512.0 MiB", preview)
        self.assertIn("40.0 GiB", preview)
        self.assertIn("Path: /dev/mapper/very-long-logical-volume-name", preview)
        self.assertIn("Label: A very long filesystem label that exceeds the table", preview)
        self.assertIn("Mounted at: /media/backup", preview)
        self.assertIn("Unallocated  192.9 GiB (207.1 GB), approx.", preview)
        free_row = next(line for line in preview.splitlines() if line.startswith("Unallocated") and "│" in line)
        self.assertEqual([part.strip() for part in free_row.split("│")],
                         ["Unallocated", "192.9 GiB", "-", "Free space"])

    def test_unknown_devices_are_not_described_as_empty(self):
        preview = tui_devices.device_details({"path": "/dev/sdz", "size": 1024**3}, 80)
        self.assertIn("Serial    -", preview)
        self.assertIn("No recognized partitions or filesystems", preview)

    def test_metadata_stays_on_one_line_and_escapes_literal_backslashes(self):
        self.disk["model"] = "Fake\tmodel\nwith\x1b controls"
        self.disk["children"][0]["label"] = r"label\n$(touch /tmp/should-not-run)"
        _, description = tui_devices.menu_items([self.disk], 100)[0]
        self.assertEqual(len(description.splitlines()), 1)
        self.assertEqual(description.count("\t"), 1)
        self.assertNotIn("\x1b", description)
        self.assertIn(r"label\\n$(touch /tmp/should-not-run)", description)


if __name__ == "__main__":
    unittest.main()
