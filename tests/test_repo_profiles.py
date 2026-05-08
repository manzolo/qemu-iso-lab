import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.config  # noqa: E402
import vmctl.state  # noqa: E402


class RepositoryProfileCatalogTests(unittest.TestCase):
    def test_repository_catalog_contains_first_wave_profiles(self):
        original_root = vmctl.state.ROOT
        original_config_dir = vmctl.state.CONFIG_DIR
        try:
            vmctl.state.ROOT = ROOT
            vmctl.state.CONFIG_DIR = ROOT / "vms"
            cfg = vmctl.config.load_config()
        finally:
            vmctl.state.ROOT = original_root
            vmctl.state.CONFIG_DIR = original_config_dir

        for profile in (
            "alpine-installed-ci",
            "debian-efi",
            "debian-bios",
            "fedora-server-efi",
            "freebsd",
        ):
            self.assertIn(profile, cfg["vms"])


if __name__ == "__main__":
    unittest.main()
