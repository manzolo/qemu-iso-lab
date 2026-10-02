"""vms/labs/_common.sh: the helpers of the lab tests, run by bash against a stub vmctl."""
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests._common import ROOT

COMMON = ROOT / "vms" / "labs" / "_common.sh"
# The stub answers `vmctl shell <vm> -- <words...>`: it records the call, prints the command it
# got and exits with the status named by the first word when that is a number.
STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$VMCTL_STUB_LOG"
[ "$1" = shell ] || exit 99
vm=$2; shift 2; [ "$1" = -- ] && shift
echo "$vm:$*"
case "$1" in [0-9]*) exit "$1";; esac
exit 0
"""


@unittest.skipUnless(os.path.exists("/bin/bash"), "needs bash")
class LabCommonTests(unittest.TestCase):
    def run_script(self, body: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "vmctl"
            stub.write_text(STUB)
            stub.chmod(0o755)
            script = Path(tmp) / "test.sh"
            script.write_text(f'source "{COMMON}"\n' + textwrap.dedent(body))
            env = {"PATH": os.defpath, "HOME": tmp, "LC_ALL": "C.UTF-8", "VMCTL": str(stub), "VMCTL_STUB_LOG": str(Path(tmp) / "calls.log")}
            result = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, env=env, check=False)
            result.calls = (Path(tmp) / "calls.log").read_text() if (Path(tmp) / "calls.log").exists() else ""  # type: ignore[attr-defined]
            return result

    def test_on_runs_the_command_through_vmctl_shell_with_the_words_as_given(self):
        result = self.run_script('on lab-server systemctl is-active "ssh daemon"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "lab-server:systemctl is-active ssh daemon\n")
        self.assertEqual(result.calls, "shell lab-server -- systemctl is-active ssh daemon\n")

    def test_the_assertions_count_and_report_results_exits_with_the_failures(self):
        result = self.run_script("""
            assert "zero passes" on vm 0 true
            assert "non-zero fails" on vm 2 false
            assert_fail "non-zero is a pass here" on vm 1
            out=$(on vm cat /etc/hostname)
            assert_contains "output seen" "$out" "^vm:cat /etc/hostname$"
            assert_not_contains "pattern absent" "$out" "nothing-here"
            assert_not_contains "pattern present fails" "$out" "hostname"
            report_results "Exercise 9"
        """)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(lines, ["[PASS] zero passes", "[FAIL] non-zero fails", "[PASS] non-zero is a pass here",
                                 "[PASS] output seen", "[PASS] pattern absent", "[FAIL] pattern present fails (unexpected: hostname)",
                                 "Exercise 9: 4 passed, 2 failed"])
        self.assertEqual(result.calls.count("\n"), 4)

    def test_all_green_reports_zero(self):
        result = self.run_script('assert "ok" on vm true\nreport_results\n')
        self.assertEqual(result.returncode, 0)
        self.assertIn("Test: all 1 checks passed", result.stdout)

    def test_the_default_binary_is_the_checkouts_vmctl(self):
        text = COMMON.read_text()
        self.assertIn('VMCTL="${VMCTL:-$(cd "$LAB_COMMON_DIR/../.." && pwd)/bin/vmctl}"', text)
        self.assertTrue((ROOT / "bin" / "vmctl").is_file())
