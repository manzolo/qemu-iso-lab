"""The oldest Python the project claims is the oldest one CI runs the suite on."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PythonSupportTests(unittest.TestCase):
    def test_ci_matrix_starts_at_requires_python(self):
        minimum = re.search(r'requires-python\s*=\s*">=(\d+\.\d+)"', (ROOT / "pyproject.toml").read_text()).group(1)
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        matrix = re.search(r"python:\s*\[([^\]]+)\]", workflow).group(1)
        versions = [version.strip().strip('"') for version in matrix.split(",")]
        self.assertEqual(versions[0], minimum)
        self.assertEqual(versions, sorted(versions, key=lambda v: tuple(map(int, v.split(".")))))


if __name__ == "__main__":
    unittest.main()
