"""The unattended install phase must be bounded: a guest that never powers off is a failure, not a hang."""

import argparse
import io
import subprocess
import sys
import time
from unittest import mock

import vmctl.lifecycle
import vmctl.runtime
from _common import BaseVmctlTestCase


class RunTimeoutTests(BaseVmctlTestCase):
    """`runtime.run(timeout_sec=...)`, the wait that held a five-hour check-vms run."""

    def sleeper(self, seconds=30):
        return [sys.executable, "-c", f"import time; time.sleep({seconds})"]

    def test_a_child_that_never_exits_is_killed_and_reported_with_its_log(self):
        stdout_log = self.root / "logs" / "install.log"
        started = time.monotonic()
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(self.vmctl.VMError) as caught:
                self.vmctl.run(self.sleeper(), stdout_log=stdout_log,
                               stderr_log=self.root / "logs" / "install.err", timeout_sec=0.5)
        message = str(caught.exception)
        self.assertIn("Timed out after 0s", message)
        self.assertIn("did not exit", message)
        self.assertIn(str(stdout_log), message)  # the console output is where you look next
        self.assertLess(time.monotonic() - started, 20, "the child was not killed promptly")

    def test_the_log_branches_without_timeout_still_wait_forever(self):
        # No timeout means the historical behaviour: a short command simply completes.
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.vmctl.run(self.sleeper(0), stdout_log=self.root / "logs" / "quick.log")

    def test_every_branch_of_run_honours_the_timeout(self):
        for kwargs in ({"quiet": True}, {}, {"stdout_log": self.root / "logs" / "a.log"}):
            with self.subTest(kwargs=sorted(kwargs)):
                with mock.patch("sys.stdout", new_callable=io.StringIO):
                    with self.assertRaises(self.vmctl.VMError):
                        self.vmctl.run(self.sleeper(), timeout_sec=0.5, **kwargs)

    def test_a_failing_command_still_raises_the_process_error_not_a_timeout(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(subprocess.CalledProcessError):
                self.vmctl.run([sys.executable, "-c", "raise SystemExit(3)"],
                               stdout_log=self.root / "logs" / "b.log", timeout_sec=30)


class InstallPhaseTimeoutTests(BaseVmctlTestCase):
    """Which flows bound the installer, and which deliberately do not."""

    def setUp(self):
        super().setUp()
        self.vm_config["autoinstall"] = {"username": "lab", "password_hash": "hash"}
        self.vm_config["cloud_init"] = {"user": "lab", "ssh_host_port": 2299}
        self.write_config_dir()
        self.create_disk()

    def install_call(self, **overrides):
        args = argparse.Namespace(vm=self.vm_name, video=None, headless=True, spice_port=None, dry_run=False)
        for key, value in overrides.items():
            setattr(args, key, value)
        with mock.patch.object(vmctl.runtime, "run") as run, \
             mock.patch.object(vmctl.lifecycle.iso, "ensure_iso", return_value=self.root / "isos/test.iso"), \
             mock.patch.object(vmctl.lifecycle.iso, "extract_installer_boot_artifacts",
                               return_value=(self.root / "k", self.root / "i")), \
             mock.patch.object(vmctl.lifecycle.cloud_init, "create_autoinstall_seed", return_value=self.root / "seed.iso"), \
             mock.patch.object(vmctl.lifecycle.qemu, "common_args", return_value=["qemu-system-x86_64"]), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.vmctl.cmd_install_unattended(args)
        return run.call_args.kwargs

    def test_a_bootstrap_bounds_the_installer(self):
        self.assertEqual(self.install_call(timeout=3600)["timeout_sec"], 3600)

    def test_running_the_installer_by_hand_stays_unbounded(self):
        # `vmctl install-unattended <vm>` has no --timeout: somebody is watching the screen.
        self.assertIsNone(self.install_call()["timeout_sec"])

    def test_the_autoinstall_bootstraps_hand_their_timeout_down(self):
        import vmctl.cli
        parser = vmctl.cli.build_parser()
        for command in ("bootstrap-unattended", "bootstrap-omarchy"):
            with self.subTest(command=command):
                args = parser.parse_args([command, self.vm_name])
                # Aligned with the flows that always bounded their install (preseed, kickstart...).
                self.assertEqual(args.timeout, 1800)
        captured = {}
        with mock.patch.object(vmctl.lifecycle, "cmd_install_unattended", side_effect=lambda a: captured.update(vars(a))), \
             mock.patch.object(vmctl.lifecycle, "prepare_background_vm_slot",
                               return_value=(self.root / "pid", self.root / "log")), \
             mock.patch.object(vmctl.lifecycle.qemu, "common_args", return_value=["qemu-system-x86_64"]), \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None), \
             mock.patch.object(vmctl.lifecycle, "run_post_install"), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.vmctl.cmd_bootstrap_unattended(parser.parse_args(["bootstrap-unattended", self.vm_name, "--timeout", "2400"]))
        self.assertEqual(captured.get("timeout"), 2400)
