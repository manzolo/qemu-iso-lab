import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.config  # noqa: E402
import vmctl.state  # noqa: E402


class RepositoryProfileCatalogTests(unittest.TestCase):
    def test_repository_catalog_contains_first_wave_profiles(self):
        # Validate the tracked catalog alone: the developer's gitignored
        # vms/profiles/local.json must not be able to change this outcome.
        original_root = vmctl.state.ROOT
        original_config_dir = vmctl.state.CONFIG_DIR
        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = Path(tmp) / "vms" / "profiles"
            profiles_dir.mkdir(parents=True)
            for profile_path in (ROOT / "vms" / "profiles").glob("*.json"):
                if profile_path.name != "local.json":
                    shutil.copy(profile_path, profiles_dir / profile_path.name)
            try:
                vmctl.state.ROOT = Path(tmp)
                vmctl.state.CONFIG_DIR = Path(tmp) / "vms"
                cfg = vmctl.config.load_config()
            finally:
                vmctl.state.ROOT = original_root
                vmctl.state.CONFIG_DIR = original_config_dir

        for profile in (
            "alpine-installed-ci",
            "debian-efi",
            "debian-bios",
            "ubuntu-server-headless",
            "fedora-server-efi",
            "freebsd",
            "arch-omarchy-nvidia-local",
        ):
            self.assertIn(profile, cfg["vms"])


if __name__ == "__main__":
    unittest.main()
