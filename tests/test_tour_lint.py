"""tools/tour/lint_commands.py over the repository: no lesson, lab exercise or guide command that
breaks in an interactive shell (a "!" in double quotes, a command that stops to ask)."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lint_commands", ROOT / "tools/tour/lint_commands.py")
assert SPEC and SPEC.loader
lint_commands = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lint_commands)


class TourLintTests(unittest.TestCase):
    def test_the_repository_has_no_command_that_breaks_interactively(self):
        report = lint_commands.lint(lint_commands.default_paths())
        self.assertEqual(report, [], "\n" + "\n".join(report))

    def test_what_it_catches(self):
        problems = lint_commands.problems
        self.assertTrue(problems('mysql -e "CREATE USER r IDENTIFIED BY \'Reader123!\';"'))  # mysql-lab, 2026-10-04
        self.assertTrue(problems("sudo mdadm --create /dev/md0 --level=1 --raid-devices=2 a b"))
        self.assertTrue(problems("sudo apt-get install git"))
        self.assertTrue(problems("sudo mkfs.ext4 /dev/md1"))
        for fine in ("echo 'Hello!'", 'echo "Done! "', 'test "$a" != "b"', "sudo mdadm --create /dev/md0 --run --level=1",
                     "sudo apt install -y git", "sudo mkfs.ext4 -F -q /dev/md1", "sudo mkfs.xfs -f /dev/vg/lv",
                     "mysql -u reader -p'readerpass' testdb    # a comment with \"!\" is not typed"):
            self.assertEqual(problems(fine), [], fine)


if __name__ == "__main__":
    unittest.main()
