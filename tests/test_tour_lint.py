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


class VariablesShownFirstTests(unittest.TestCase):
    def test_a_variable_is_shown_before_it_is_used(self):
        unset = lint_commands.unset_variables
        self.assertEqual(unset(["echo $DISKS"]), ["$DISKS is used before the clip shows where it comes from: echo $DISKS"])
        self.assertTrue(unset(["sudo zpool create tank mirror $1 $2"]))  # positional names need a set --
        shown = [
            "DISKS=$(lsblk -dnpo NAME,SIZE | awk '$2==\"2G\" {print $1}')",  # awk's $2 and $1 are not the shell's
            "echo $DISKS", "set -- $DISKS", "sudo zpool create tank mirror $1 $2",
            "for d in $DISKS; do sudo wipefs -a $d; done", "echo $HOME",
            "kubectl get pods    # a comment may mention $ANYTHING",
        ]
        self.assertEqual(unset(shown), [])


if __name__ == "__main__":
    unittest.main()
