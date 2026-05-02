import argparse
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl  # noqa: E402
import vmctl.cloud_init  # noqa: E402
import vmctl.iso  # noqa: E402
import vmctl.lifecycle  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.ssh  # noqa: E402
import vmctl.host_setup  # noqa: E402
import vmctl.qemu  # noqa: E402
import vmctl.state  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class ManageTests(BaseVmctlTestCase):
    def test_cmd_stop_removes_stale_pid_file(self):
        pid_path = self.root / "artifacts/testvm/runtime/bootstrap-start.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("424242\n", encoding="utf-8")
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        exit_code = self.vmctl.cmd_stop(args)

        self.assertEqual(exit_code, 0)
        self.assertFalse(pid_path.exists())

    def test_cmd_shell_runs_interactive_ssh_command(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }
        self.write_config_dir()

        with mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_shell(argparse.Namespace(vm=self.vm_name, dry_run=True))

        self.assertEqual(exit_code, 0)
        ssh_cmd = run_cmd.call_args.args[0]
        self.assertEqual(ssh_cmd[0], "ssh")
        self.assertNotIn("BatchMode=yes", ssh_cmd)
        self.assertEqual(ssh_cmd[-1], "tester@127.0.0.1")

    def test_cmd_shell_runs_with_ssh_provision(self):
        self.vm_config["ssh_provision"] = {
            "user": "tester",
            "ssh_host_port": 2223,
        }
        self.write_config_dir()

        with mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_shell(argparse.Namespace(vm=self.vm_name, dry_run=True))

        self.assertEqual(exit_code, 0)
        ssh_cmd = run_cmd.call_args.args[0]
        self.assertEqual(ssh_cmd[0], "ssh")
        self.assertNotIn("BatchMode=yes", ssh_cmd)
        self.assertEqual(ssh_cmd[-1], "tester@127.0.0.1")

    def test_cmd_stop_terminates_running_qemu_pid(self):
        pid_path = self.root / "artifacts/testvm/runtime/bootstrap-start.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("1234\n", encoding="utf-8")
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(vmctl.lifecycle, "process_cmdline", side_effect=["qemu-system-x86_64 -display none", "qemu-system-x86_64 -display none", None]), \
             mock.patch.object(os, "kill") as kill_mock, \
             mock.patch.object(time, "sleep") as sleep_mock:
            exit_code = self.vmctl.cmd_stop(args)

        self.assertEqual(exit_code, 0)
        kill_mock.assert_called_once_with(1234, self.vmctl.signal.SIGTERM)
        self.assertLessEqual(sleep_mock.call_count, 1)
        self.assertFalse(pid_path.exists())

    def test_cmd_stop_terminates_discovered_qemu_when_pid_file_missing(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(vmctl.lifecycle, "find_qemu_process_by_hostfwd_port", return_value=(5678, "qemu-system-x86_64 -netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22")), \
             mock.patch.object(vmctl.lifecycle, "process_cmdline", side_effect=["qemu-system-x86_64 -netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22", None]), \
             mock.patch.object(os, "kill") as kill_mock, \
             mock.patch.object(time, "sleep") as sleep_mock:
            exit_code = self.vmctl.cmd_stop(args)

        self.assertEqual(exit_code, 0)
        kill_mock.assert_called_once_with(5678, self.vmctl.signal.SIGTERM)
        self.assertLessEqual(sleep_mock.call_count, 1)

    def test_cmd_stop_falls_back_to_discovered_qemu_after_stale_pid_file(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }
        self.write_config_dir()
        pid_path = self.root / "artifacts/testvm/runtime/bootstrap-start.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("424242\n", encoding="utf-8")
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(vmctl.lifecycle, "find_qemu_process_by_hostfwd_port", return_value=(5678, "qemu-system-x86_64 -netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22")), \
             mock.patch.object(vmctl.lifecycle, "process_cmdline", side_effect=[None, "qemu-system-x86_64 -netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22", None]), \
             mock.patch.object(os, "kill") as kill_mock, \
             mock.patch.object(time, "sleep") as sleep_mock:
            exit_code = self.vmctl.cmd_stop(args)

        self.assertEqual(exit_code, 0)
        self.assertFalse(pid_path.exists())
        kill_mock.assert_called_once_with(5678, self.vmctl.signal.SIGTERM)
        self.assertLessEqual(sleep_mock.call_count, 1)

    def test_cmd_clean_removes_generated_artifact_subdirs(self):
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        disk_path = self.create_disk()
        vars_path = self.root / self.vm_config["firmware"]["vars_path"]
        vars_path.parent.mkdir(parents=True, exist_ok=True)
        vars_path.write_text("vars", encoding="utf-8")
        for relative in [
            "artifacts/testvm/runtime/bootstrap-start.pid",
            "artifacts/testvm/logs/bootstrap-start.log",
            "artifacts/testvm/cloud-init/seed.iso",
            "artifacts/testvm/autoinstall/seed.iso",
            "artifacts/testvm/installer/vmlinuz",
        ]:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("artifact", encoding="utf-8")

        with mock.patch.object(vmctl.lifecycle, "cmd_stop", return_value=0):
            exit_code = self.vmctl.cmd_clean(argparse.Namespace(vm=self.vm_name, all=False, dry_run=False))

        self.assertEqual(exit_code, 0)
        self.assertFalse(disk_path.exists())
        self.assertFalse(vars_path.exists())
        self.assertFalse((self.root / "artifacts/testvm/runtime").exists())
        self.assertFalse((self.root / "artifacts/testvm/logs").exists())
        self.assertFalse((self.root / "artifacts/testvm/cloud-init").exists())
        self.assertFalse((self.root / "artifacts/testvm/autoinstall").exists())
        self.assertFalse((self.root / "artifacts/testvm/installer").exists())

    def test_cmd_clean_stops_vm_before_removing_artifacts(self):
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        self.create_disk()

        with mock.patch.object(vmctl.lifecycle, "cmd_stop", return_value=0) as stop_cmd:
            exit_code = self.vmctl.cmd_clean(argparse.Namespace(vm=self.vm_name, all=False, dry_run=False))

        self.assertEqual(exit_code, 0)
        stop_cmd.assert_called_once()
        stop_args = stop_cmd.call_args.args[0]
        self.assertEqual(stop_args.vm, self.vm_name)
        self.assertFalse(stop_args.dry_run)

    def test_cmd_delete_iso_removes_cached_iso_and_partial_download(self):
        iso_path = self.root / self.vm_config["iso"]
        partial_path = iso_path.with_name(iso_path.name + ".part")
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("iso", encoding="utf-8")
        partial_path.write_text("partial", encoding="utf-8")

        exit_code = self.vmctl.cmd_delete_iso(argparse.Namespace(vm=self.vm_name, dry_run=False))

        self.assertEqual(exit_code, 0)
        self.assertFalse(iso_path.exists())
        self.assertFalse(partial_path.exists())

    def test_cmd_delete_iso_dry_run_keeps_cached_iso(self):
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("iso", encoding="utf-8")

        exit_code = self.vmctl.cmd_delete_iso(argparse.Namespace(vm=self.vm_name, dry_run=True))

        self.assertEqual(exit_code, 0)
        self.assertTrue(iso_path.exists())

    def test_firmware_args_uses_common_ovmf_fallback_when_configured_paths_are_missing(self):
        self.create_disk()
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "/missing/OVMF_CODE_4M.fd",
            "vars_template": "/missing/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()

        fallback_code = self.root / "firmware" / "OVMF_CODE_4M.fd"
        fallback_vars = self.root / "firmware" / "OVMF_VARS_4M.fd"
        fallback_code.parent.mkdir(parents=True, exist_ok=True)
        fallback_code.write_text("code", encoding="utf-8")
        fallback_vars.write_text("vars", encoding="utf-8")

        with mock.patch.object(vmctl.state, "COMMON_OVMF_PAIRS",
            [(str(fallback_code), str(fallback_vars))],
        ):
            qemu_fw_args = self.vmctl.firmware_args(self.vm_config)

        self.assertIn(f"if=pflash,format=raw,readonly=on,file={fallback_code}", qemu_fw_args)
        self.assertIn(
            f"if=pflash,format=raw,file={self.root / 'artifacts/testvm/OVMF_VARS.fd'}",
            qemu_fw_args,
        )
        self.assertEqual((self.root / "artifacts/testvm/OVMF_VARS.fd").read_text(encoding="utf-8"), "vars")

    def test_cmd_setup_reports_missing_dependencies_and_install_hints(self):
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "/missing/OVMF_CODE_4M.fd",
            "vars_template": "/missing/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        args = argparse.Namespace()

        def fake_which(name):
            if name in {"qemu-system-x86_64", "python3"}:
                return f"/usr/bin/{name}"
            return None

        with mock.patch.object(shutil, "which", side_effect=fake_which), \
             mock.patch.object(vmctl.state, "COMMON_OVMF_PAIRS", []), \
             mock.patch.object(vmctl.host_setup, "read_os_release", return_value={"ID": "ubuntu", "ID_LIKE": "debian"}), \
             mock.patch("sys.stdout", new_callable=mock.MagicMock()) as stdout:
            exit_code = self.vmctl.cmd_setup(args)

        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertEqual(exit_code, 1)
        self.assertIn("[missing] qemu-img", output)
        self.assertIn("Unable to locate OVMF firmware files for EFI guest.", output)
        self.assertIn("Affected EFI profiles: testvm", output)
        self.assertIn("sudo apt install -y qemu-system-x86 qemu-utils ovmf python3 make dialog cloud-image-utils xorriso", output)

    def test_cmd_setup_can_install_missing_packages_after_confirmation(self):
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        firmware_dir = self.root / "firmware"
        firmware_dir.mkdir(parents=True, exist_ok=True)
        (firmware_dir / "OVMF_CODE_4M.fd").write_text("code", encoding="utf-8")
        (firmware_dir / "OVMF_VARS_4M.fd").write_text("vars", encoding="utf-8")
        args = argparse.Namespace()

        which_calls = {"count": 0}

        def fake_which(name):
            which_calls["count"] += 1
            if which_calls["count"] <= 4:
                if name in {"qemu-system-x86_64", "python3"}:
                    return f"/usr/bin/{name}"
                return None
            return "/usr/bin/fake"

        with mock.patch.object(shutil, "which", side_effect=fake_which), \
             mock.patch.object(vmctl.host_setup, "prompt_yes_no", return_value=True), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd, \
             mock.patch("sys.stdout", new_callable=mock.MagicMock()):
            exit_code = self.vmctl.cmd_setup(args)

        self.assertEqual(exit_code, 0)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0], ["sudo", "apt", "update"])
        self.assertEqual(
            executed[1],
            ["sudo", "apt", "install", "-y", "qemu-system-x86", "qemu-utils", "ovmf", "python3", "make", "dialog", "cloud-image-utils", "xorriso"],
        )

    def test_cmd_setup_passes_when_requirements_and_firmware_are_available(self):
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        firmware_dir = self.root / "firmware"
        firmware_dir.mkdir(parents=True, exist_ok=True)
        (firmware_dir / "OVMF_CODE_4M.fd").write_text("code", encoding="utf-8")
        (firmware_dir / "OVMF_VARS_4M.fd").write_text("vars", encoding="utf-8")
        args = argparse.Namespace()

        with mock.patch.object(shutil, "which", return_value="/usr/bin/fake"), \
             mock.patch("sys.stdout", new_callable=mock.MagicMock()) as stdout:
            exit_code = self.vmctl.cmd_setup(args)

        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertEqual(exit_code, 0)
        self.assertIn("Setup check passed.", output)


if __name__ == "__main__":
    unittest.main()
