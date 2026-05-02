import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._common import BaseVmctlTestCase  # noqa: E402


class DiskInspectTests(BaseVmctlTestCase):
    def test_maybe_read_gpt_geometry_reads_entry_array_metadata(self):
        sector_size = 512
        disk_path = self.root / "gpt-header.img"
        header = bytearray(sector_size * 2)
        header[sector_size:sector_size + 8] = b"EFI PART"
        header[sector_size + 40:sector_size + 48] = (2048).to_bytes(8, "little")
        header[sector_size + 80:sector_size + 84] = (1024).to_bytes(4, "little")
        header[sector_size + 84:sector_size + 88] = (128).to_bytes(4, "little")
        disk_path.write_bytes(header)

        geometry = self.vmctl.maybe_read_gpt_geometry(str(disk_path), sector_size)

        self.assertEqual(
            geometry,
            {
                "gpt_first_usable_lba": 2048,
                "gpt_partition_entry_count": 1024,
                "gpt_partition_entry_size": 128,
            },
        )


if __name__ == "__main__":
    unittest.main()
