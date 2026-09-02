import argparse
import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl  # noqa: E402
import vmctl.state  # noqa: E402
from tests._common import BaseVmctlTestCase  # noqa: E402


class CliSmokeTests(unittest.TestCase):
    def test_bin_shim_runs_help(self):
        result = subprocess.run(
            [sys.executable, str(vmctl.state.ROOT / "bin" / "vmctl"), "--help"],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("list", result.stdout)
        self.assertIn("install", result.stdout)
        self.assertIn("check-vms", result.stdout)

    def test_version_flag_prints_version(self):
        result = subprocess.run(
            [sys.executable, str(vmctl.state.ROOT / "bin" / "vmctl"), "--version"],
            capture_output=True, text=True,
        )
        self.assertIn(vmctl.__version__, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()


class CliHelpLayoutTests(unittest.TestCase):
    def setUp(self):
        import vmctl.cli
        self.cli = vmctl.cli
        self.parser = self.cli.build_parser()

    def _public_subcommands(self):
        action = next(a for a in self.parser._actions if isinstance(a, argparse._SubParsersAction))
        names = set()
        for name in action.choices:
            if name.startswith("_") or name == "test-local":  # internal or alias
                continue
            names.add(name)
        return names

    def test_every_public_subcommand_is_in_exactly_one_group(self):
        grouped = self.cli.public_commands()
        self.assertEqual(len(grouped), len(set(grouped)), "a command is listed in two groups")
        self.assertEqual(set(grouped), self._public_subcommands())

    def test_help_is_grouped_by_task_and_ends_with_typical_flows(self):
        text = self.parser.format_help()
        for title, _, _ in self.cli.COMMAND_GROUPS:
            self.assertIn(f"{title}:", text)
        self.assertIn("typical flows:", text)
        self.assertIn("vmctl provision <vm>", text)
        self.assertLess(text.index("Discover:"), text.index("options:"))

    def test_subcommand_help_carries_its_description(self):
        sub = self.parser._subparsers._group_actions[0].choices["start"]  # type: ignore[union-attr]
        self.assertIn("boot the installed disk", sub.format_help())

    def test_completion_scripts_mention_every_command(self):
        for shell, marker in (("zsh", "#compdef vmctl"), ("bash", "complete -F _vmctl vmctl")):
            with self.subTest(shell=shell):
                out = io.StringIO()
                args = self.parser.parse_args(["completion", shell])
                with contextlib.redirect_stdout(out):
                    self.assertEqual(args.func(args), 0)
                text = out.getvalue()
                self.assertIn(marker, text)
                for name in self.cli.public_commands():
                    self.assertIn(name, text)
                self.assertIn("vmctl list --names", text)


class CliListNamesTests(BaseVmctlTestCase):
    def test_list_names_prints_one_profile_per_line(self):
        self.write_extra_profile("zeta.json", {"vms": {"zeta": dict(self.vm_config, name="Zeta")}})
        import vmctl.cli
        parser = vmctl.cli.build_parser()
        args = parser.parse_args(["list", "--names"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(args.func(args), 0)
        self.assertEqual(out.getvalue().splitlines(), [self.vm_name, "zeta"])
