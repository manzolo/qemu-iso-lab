import argparse
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.errors  # noqa: E402
import vmctl.lifecycle  # noqa: E402
import vmctl.qemu  # noqa: E402
import vmctl.runtime  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class SerialSocketArgsTests(BaseVmctlTestCase):
    def test_serial_socket_chardev_logs_and_serves_without_waiting(self):
        sock = self.root / "artifacts/testvm/runtime/serial.sock"
        log = self.root / "artifacts/testvm/logs/serial.log"
        args = vmctl.qemu.serial_socket_args(sock, log)
        self.assertEqual(args[0], "-chardev")
        self.assertEqual(args[1], f"socket,id=char0,path={sock},server=on,wait=off,logfile={log},logappend=on")
        self.assertEqual(args[2:], ["-serial", "chardev:char0"])
        self.assertTrue(sock.parent.is_dir() and log.parent.is_dir())
        self.assertNotIn("logfile", vmctl.qemu.serial_socket_args(sock, None)[1])
        self.assertEqual(vmctl.qemu.serial_socket_path(self.vm_config), sock)

    def test_common_args_serial_socket_replaces_stdio_and_is_exclusive_with_it(self):
        self.create_disk()
        sock = vmctl.qemu.serial_socket_path(self.vm_config)
        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-system-x86_64"):
            args = vmctl.qemu.common_args(self.vm_config, None, headless=True, serial_socket=sock, serial_log=self.root / "s.log")
            with self.assertRaises(vmctl.errors.VMError):
                vmctl.qemu.common_args(self.vm_config, None, headless=True, serial_stdio=True, serial_socket=sock)
        joined = " ".join(args)
        self.assertIn(f"socket,id=char0,path={sock},server=on,wait=off,logfile=", joined)
        self.assertNotIn("stdio,id=char0", joined)
        self.assertEqual(args.count("-serial"), 1)


class ConsoleCommandTests(BaseVmctlTestCase):
    def test_background_start_exposes_the_serial_socket(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, headless=True, background=True, cloud_init=False, spice_port=None, dry_run=False)
        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-system-x86_64"), \
             mock.patch.object(vmctl.lifecycle, "is_bootstrap_vm_running", return_value=(False, None, None)), \
             mock.patch.object(vmctl.runtime, "run_background", return_value=4242) as run_background:
            self.assertEqual(self.vmctl.cmd_start(args), 0)
        cmd = run_background.call_args.args[0]
        self.assertTrue(any(f"path={vmctl.qemu.serial_socket_path(self.vm_config)}" in a for a in cmd))
        self.assertTrue(any(f"logfile={self.root / 'artifacts/testvm/logs/serial.log'}" in a for a in cmd))
        self.assertNotIn("stdio,id=char0,signal=off", cmd)

    def test_post_install_background_boot_keeps_its_serial_log_but_on_the_socket(self):
        self.create_disk()
        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-system-x86_64"), \
             mock.patch.object(vmctl.lifecycle, "prepare_background_vm_slot", return_value=(self.root / "a.pid", self.root / "a.log")), \
             mock.patch.object(vmctl.runtime, "run_background", return_value=1) as run_background:
            self.vmctl.start_installed_vm_headless(self.vm_name, self.vm_config, True)
        cmd = run_background.call_args.args[0]
        self.assertTrue(any(f"logfile={self.root / 'artifacts/testvm/logs/post-install-serial.log'}" in a for a in cmd))
        self.assertFalse(any(a.startswith("file:") for a in cmd))

    def test_cmd_console_dry_run_not_running_and_no_socket(self):
        self.assertEqual(self.vmctl.cmd_console(argparse.Namespace(vm=self.vm_name, dry_run=True)), 0)
        with mock.patch.object(vmctl.lifecycle, "running_qemu_pid", return_value=None), self.assertRaises(vmctl.errors.VMError) as ctx:
            self.vmctl.cmd_console(argparse.Namespace(vm=self.vm_name, dry_run=False))
        self.assertIn("not running", str(ctx.exception))
        with mock.patch.object(vmctl.lifecycle, "running_qemu_pid", return_value=77), self.assertRaises(vmctl.errors.VMError) as ctx:
            self.vmctl.cmd_console(argparse.Namespace(vm=self.vm_name, dry_run=False))
        self.assertIn("no serial socket", str(ctx.exception))
        sock = vmctl.qemu.serial_socket_path(self.vm_config)
        sock.parent.mkdir(parents=True)
        sock.write_bytes(b"")
        with mock.patch.object(vmctl.lifecycle, "running_qemu_pid", return_value=77), \
             mock.patch.object(vmctl.qemu, "serial_console") as console:
            self.assertEqual(self.vmctl.cmd_console(argparse.Namespace(vm=self.vm_name, dry_run=False)), 0)
        console.assert_called_once_with(sock)

    def test_serial_console_refuses_without_a_tty(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False), self.assertRaises(vmctl.errors.VMError):
            vmctl.qemu.serial_console(self.root / "nope.sock")

    def test_cli_registers_console_in_the_run_group(self):
        import vmctl.cli
        parser = vmctl.cli.build_parser()
        args = parser.parse_args(["console", self.vm_name])
        self.assertIs(args.func, vmctl.lifecycle.cmd_console)
        self.assertIn("console", dict((title, names) for title, _, names in vmctl.cli.COMMAND_GROUPS)["Run"])


if __name__ == "__main__":
    unittest.main()
