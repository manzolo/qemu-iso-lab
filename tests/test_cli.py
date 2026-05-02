import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl  # noqa: E402
import vmctl.state  # noqa: E402


class CliSmokeTests(unittest.TestCase):
    def test_bin_shim_runs_help(self):
        result = subprocess.run(
            [sys.executable, str(vmctl.state.ROOT / "bin" / "vmctl"), "--help"],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("list", result.stdout)
        self.assertIn("install", result.stdout)

    def test_version_flag_prints_version(self):
        result = subprocess.run(
            [sys.executable, str(vmctl.state.ROOT / "bin" / "vmctl"), "--version"],
            capture_output=True, text=True,
        )
        self.assertIn(vmctl.__version__, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
