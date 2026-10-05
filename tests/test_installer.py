"""Exercise the Linux entry point without network, sudo or host package changes."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/manzolo/qemu-iso-lab.git"
STUB = r'''
import json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as log:
    log.write(json.dumps([name, args]) + "\n")
if name == os.environ.get("FAIL_TOOL"):
    sys.exit(17)
if name == "uname":
    print("Linux")
elif name == "id":
    print("1000")
elif name == "python3":
    sys.exit(int(os.environ.get("PYTHON_EXIT", "0")))
elif name == "sudo":
    sys.exit(subprocess.call(args))
elif name in ("apt-get", "pacman", "dnf", "zypper"):
    if "install" in args or "-S" in args:
        for tool in ("git", "python3"):
            path = pathlib.Path(os.environ["PATH"]) / tool
            if not path.exists():
                path.write_bytes(pathlib.Path(__file__).read_bytes())
                path.chmod(0o755)
elif name == "git":
    if args[0] == "clone":
        dest = pathlib.Path(args[-1])
        (dest / ".git").mkdir(parents=True)
        setup = dest / "setup.sh"
        setup.write_bytes(pathlib.Path(__file__).read_bytes())
        setup.chmod(0o755)
        (dest / "bin").mkdir()
        launcher = dest / "bin/qemu-iso-lab"
        launcher.write_text("#!/bin/sh\n")
        launcher.chmod(0o755)
    elif args[2:] == ["remote", "get-url", "origin"]:
        print(os.environ.get("ORIGIN", "https://github.com/manzolo/qemu-iso-lab.git"))
    else:
        sys.exit("Unexpected git invocation")
elif name == "setup.sh" and os.environ.get("READ_ANSWER"):
    print("answer=" + input())
'''


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.target = self.root / "home with spaces" / "qemu-iso-lab"
        self.log = self.root / "calls.jsonl"
        self.env = {
            **os.environ, "PATH": str(self.bin), "HOME": str(self.target.parent),
            "VMCTL_INSTALL_DIR": str(self.target), "CALLS": str(self.log),
        }
        for tool in ("uname", "id", "git", "python3", "sudo", "apt-get"):
            self.tool(tool)

    def tool(self, name):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + STUB)
        path.chmod(0o755)

    def run_installer(self, *args, answer=""):
        return subprocess.run(
            ["/bin/sh", "-c", (ROOT / "install.sh").read_text(), "install.sh", *args],
            env=self.env, input=answer, text=True, capture_output=True, timeout=15,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_fresh_install_preserves_stdin_and_forwards_setup_arguments(self):
        self.env["READ_ANSWER"] = "1"
        result = self.run_installer("--yes", answer="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("answer=y", result.stdout)
        self.assertIn(["git", ["clone", REPO, str(self.target)]], self.calls())
        self.assertIn(["setup.sh", ["--yes"]], self.calls())
        self.assertNotIn("sudo", [name for name, _ in self.calls()])
        # setup.sh ends with how to start the lab; the installer adds nothing on a current checkout.
        self.assertNotIn("Open the dashboard", result.stdout)
        self.assertNotIn("Update your checkout", result.stdout)

    def test_rerun_reuses_checkout_without_pull_or_clone(self):
        self.assertEqual(self.run_installer().returncode, 0)
        self.log.write_text("")
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([args for name, args in self.calls() if name == "git"],
                         [["-C", str(self.target), "remote", "get-url", "origin"]])
        self.assertIn(["setup.sh", []], self.calls())

    def test_unrelated_directory_is_preserved(self):
        self.target.mkdir(parents=True)
        keep = self.target / "keep"
        keep.write_text("local work")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(keep.read_text(), "local work")
        self.assertNotIn("setup.sh", [name for name, _ in self.calls()])

    def test_older_checkout_gets_a_working_start_command_without_updating_it(self):
        self.assertEqual(self.run_installer().returncode, 0)
        (self.target / "bin/qemu-iso-lab").unlink()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'"{self.target}/bin/vmctl" web --open', result.stdout)
        self.assertIn("Update your checkout", result.stdout)
        self.assertNotIn("Open QEMU ISO Lab from your applications menu", result.stdout)

    def test_wrong_origin_is_rejected(self):
        self.assertEqual(self.run_installer().returncode, 0)
        self.log.write_text("")
        self.env["ORIGIN"] = "https://example.org/unrelated.git"
        self.assertNotEqual(self.run_installer().returncode, 0)
        self.assertNotIn("setup.sh", [name for name, _ in self.calls()])

    def test_checkout_cloned_over_ssh_is_the_same_project(self):
        # The guides clone with git@github.com:...; a rerun must reuse that checkout, not refuse it.
        self.assertEqual(self.run_installer().returncode, 0)
        for origin in ("git@github.com:manzolo/qemu-iso-lab.git", "ssh://git@github.com/manzolo/qemu-iso-lab",
                       "https://github.com/manzolo/qemu-iso-lab"):
            with self.subTest(origin=origin):
                self.log.write_text("")
                self.env["ORIGIN"] = origin
                result = self.run_installer()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(["setup.sh", []], self.calls())

    def test_missing_prerequisites_are_installed_before_clone(self):
        managers = (("apt-get", "python3"), ("pacman", "python"), ("dnf", "python3"), ("zypper", "python3"))
        for manager, python_package in managers:
            with self.subTest(manager=manager):
                for tool in ("git", "python3", *(name for name, _ in managers)):
                    (self.bin / tool).unlink(missing_ok=True)
                self.tool(manager)
                self.log.write_text("")
                self.env["VMCTL_INSTALL_DIR"] = str(self.root / manager)
                result = self.run_installer()
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls()
                install = next(i for i, (name, args) in enumerate(calls)
                               if name == manager and python_package in args)
                clone = next(i for i, (name, args) in enumerate(calls) if name == "git" and args[0] == "clone")
                self.assertLess(install, clone)
                self.assertIn("sudo", [name for name, _ in calls])

    def test_failures_stop_before_later_steps(self):
        for tool in ("git", "setup.sh", "apt-get"):
            with self.subTest(tool=tool):
                self.env["VMCTL_INSTALL_DIR"] = str(self.root / ("fail-" + tool))
                self.env["FAIL_TOOL"] = tool
                if tool == "apt-get":
                    (self.bin / "git").unlink()
                self.log.write_text("")
                result = self.run_installer()
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertNotIn("Open QEMU ISO Lab", result.stdout)
                if tool != "setup.sh":
                    self.assertNotIn("setup.sh", [name for name, _ in self.calls()])

    def test_old_python_stops_before_clone(self):
        self.env["PYTHON_EXIT"] = "1"
        self.assertNotEqual(self.run_installer().returncode, 0)
        self.assertNotIn("git", [name for name, _ in self.calls()])

    def test_one_liner_downloads_before_executing_and_keeps_terminal_input(self):
        # Substitute only curl's transport; exercise the exact README command structure.
        curl = self.bin / "curl"
        curl.write_text(f"#!/bin/sh\nexec /bin/cat {shlex.quote(str(ROOT / 'install.sh'))}\n")
        curl.chmod(0o755)
        (self.bin / "sh").symlink_to("/bin/sh")
        self.env["READ_ANSWER"] = "1"
        from tools.build_catalog_site import LINUX_INSTALL
        result = subprocess.run(["/bin/sh", "-c", LINUX_INSTALL], env=self.env,
                                input="yes\n", capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("answer=yes", result.stdout)
