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

    def test_cmd_clean_stale_removes_dead_bootstrap_pid_file(self):
        pid_path = self.root / "artifacts/testvm/runtime/bootstrap-start.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("424242\n", encoding="utf-8")

        exit_code = self.vmctl.cmd_clean_stale(argparse.Namespace(vm=self.vm_name, dry_run=False))

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

    def test_ssh_base_cmd_uses_generated_key_for_cloud_init_access(self):
        generated_private = self.root / "artifacts/testvm/ssh/id_ed25519"
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }

        with mock.patch.object(vmctl.ssh, "ensure_generated_ssh_keypair", return_value=generated_private):
            ssh_cmd = vmctl.ssh.ssh_base_cmd(self.vm_config)

        self.assertIn("-i", ssh_cmd)
        self.assertIn(str(generated_private), ssh_cmd)
        self.assertIn("BatchMode=yes", ssh_cmd)

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

    def test_cmd_stop_force_kills_qemu_after_sigterm_timeout(self):
        pid_path = self.root / "artifacts/testvm/runtime/bootstrap-start.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("1234\n", encoding="utf-8")
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        side_effects = ["qemu-system-x86_64 -display none"] * 4 + [None]
        with mock.patch.object(vmctl.lifecycle, "process_cmdline", side_effect=side_effects), \
             mock.patch.object(time, "monotonic", side_effect=[0, 0, 16, 16, 16, 16, 16]), \
             mock.patch.object(os, "kill") as kill_mock, \
             mock.patch.object(time, "sleep") as sleep_mock:
            exit_code = self.vmctl.cmd_stop(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            kill_mock.call_args_list,
            [
                mock.call(1234, self.vmctl.signal.SIGTERM),
                mock.call(1234, self.vmctl.signal.SIGKILL),
            ],
        )
        self.assertFalse(pid_path.exists())

    def test_process_cmdline_returns_none_for_empty_proc_cmdline(self):
        with mock.patch.object(Path, "read_bytes", return_value=b""):
            self.assertIsNone(vmctl.lifecycle.process_cmdline(1234))

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

    def test_a_foreign_qemu_on_the_same_host_port_is_not_this_vm(self):
        """Another lab forwarding 2222 must never be taken for ours: vmctl stop would kill it."""
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.write_config_dir()
        foreign = ("qemu-system-x86_64 -drive file=/home/user/otherlab/disk.qcow2 "
                   "-netdev user,id=net0,hostfwd=tcp::2222-:22")
        ours = (f"qemu-system-x86_64 -drive file={self.root}/artifacts/testvm/disk.qcow2 "
                "-netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22")
        owner = self.vmctl.vm_owner_paths(self.vm_name, self.vm_config)

        with mock.patch.object(vmctl.lifecycle, "iter_qemu_processes", side_effect=lambda: iter([(4242, foreign)])):
            self.assertEqual(self.vmctl.find_qemu_process_by_hostfwd_port(2222, owner), (None, None))
            # without owner paths the caller is only asking who holds the port
            self.assertEqual(self.vmctl.find_qemu_process_by_hostfwd_port(2222)[0], 4242)
        with mock.patch.object(vmctl.lifecycle, "iter_qemu_processes", side_effect=lambda: iter([(4242, foreign), (5678, ours)])):
            self.assertEqual(self.vmctl.find_qemu_process_by_hostfwd_port(2222, owner)[0], 5678)

    def test_cmd_stop_leaves_a_foreign_qemu_alone_and_says_who_holds_the_port(self):
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.write_config_dir()
        foreign = ("qemu-system-x86_64 -drive file=/home/user/otherlab/disk.qcow2 "
                   "-netdev user,id=net0,hostfwd=tcp::2222-:22")
        args = argparse.Namespace(vm=self.vm_name, dry_run=False, force=True)

        with mock.patch.object(vmctl.lifecycle, "iter_qemu_processes", side_effect=lambda: iter([(4242, foreign)])), \
             mock.patch.object(vmctl.lifecycle, "stop_qemu_process") as stop, \
             mock.patch.object(os, "kill") as kill_mock, \
             mock.patch.object(vmctl.lifecycle.ui, "print_status") as status:
            self.assertEqual(self.vmctl.cmd_stop(args), 0)

        stop.assert_not_called()
        kill_mock.assert_not_called()
        warned = [call.args[1] for call in status.call_args_list]
        self.assertTrue(any("forwarded by another QEMU (pid 4242)" in line for line in warned), warned)

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
             mock.patch.object(vmctl.lifecycle, "process_cmdline", side_effect=[None, None, "qemu-system-x86_64 -netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22", None]), \
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
            "artifacts/testvm/ssh/id_ed25519",
            "artifacts/testvm/ssh/id_ed25519.pub",
            "artifacts/testvm/archinstall/bootstrap.iso",
            "artifacts/testvm/omarchy/seed.iso",
            "artifacts/testvm/cloud-init/seed.iso",
            "artifacts/testvm/autoinstall/seed.iso",
            "artifacts/testvm/unattended/seed.iso",
            "artifacts/testvm/installer/vmlinuz",
            # flow media the old explicit list missed, and a stray file
            "artifacts/testvm/windowsxp/install.iso",
            "artifacts/testvm/install-media/setup.iso",
            "artifacts/testvm/libvirt/domain.xml",
            "artifacts/testvm/notes.txt",
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
        self.assertFalse((self.root / "artifacts/testvm/ssh").exists())
        self.assertFalse((self.root / "artifacts/testvm/archinstall").exists())
        self.assertFalse((self.root / "artifacts/testvm/omarchy").exists())
        self.assertFalse((self.root / "artifacts/testvm/cloud-init").exists())
        self.assertFalse((self.root / "artifacts/testvm/autoinstall").exists())
        self.assertFalse((self.root / "artifacts/testvm/unattended").exists())
        self.assertFalse((self.root / "artifacts/testvm/installer").exists())
        self.assertFalse((self.root / "artifacts/testvm").exists())  # flow media and strays too

    def test_cmd_clean_stops_vm_before_removing_artifacts(self):
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        for all_vms in (False, True):
            for dry_run in (False, True):
                with self.subTest(all_vms=all_vms, dry_run=dry_run):
                    disk_path = self.create_disk()

                    def stop(args):
                        self.assertTrue(disk_path.exists())
                        self.assertEqual(args.vm, self.vm_name)
                        self.assertEqual(args.dry_run, dry_run)
                        self.assertTrue(args.force)
                        return 0

                    with mock.patch.object(vmctl.lifecycle, "cmd_stop", side_effect=stop) as stop_cmd:
                        exit_code = self.vmctl.cmd_clean(argparse.Namespace(
                            vm=None if all_vms else self.vm_name, all=all_vms, dry_run=dry_run,
                        ))

                    self.assertEqual(exit_code, 0)
                    stop_cmd.assert_called_once()
                    self.assertEqual(disk_path.exists(), dry_run)

    def test_cmd_clean_preserves_artifacts_when_stop_fails(self):
        for all_vms in (False, True):
            with self.subTest(all_vms=all_vms):
                disk_path = self.create_disk()
                with mock.patch.object(vmctl.lifecycle, "cmd_stop", side_effect=self.vmctl.VMError("stop failed")), \
                     self.assertRaisesRegex(self.vmctl.VMError, "stop failed"):
                    self.vmctl.cmd_clean(argparse.Namespace(
                        vm=None if all_vms else self.vm_name, all=all_vms, dry_run=False,
                    ))
                self.assertTrue(disk_path.exists())

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
            with mock.patch.object(vmctl.host_setup, "textual_python", return_value="python3"):
                exit_code = self.vmctl.cmd_setup(args)

        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertEqual(exit_code, 1)
        self.assertIn("[missing] qemu-img", output)
        self.assertIn("Unable to locate OVMF firmware files for EFI guest.", output)
        self.assertIn("Affected EFI profiles: testvm", output)
        self.assertIn("sudo env NEEDRESTART_MODE=l apt install -y qemu-system-x86 qemu-utils ovmf python3 openssh-client libvirt-clients libvirt-daemon-system make dialog fzf cloud-image-utils xorriso virtiofsd virt-viewer p7zip-full dvd+rw-tools python3-bcrypt swtpm gdisk", output)
        self.assertIn("[missing] virtiofsd", output)
        self.assertIn("[missing] 7z", output)

    def test_cmd_setup_finds_virtiofsd_outside_path_and_7z_variants(self):
        with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/7zz" if name == "7zz" else None), \
             mock.patch.object(vmctl.qemu, "find_virtiofsd", return_value="/usr/libexec/virtiofsd"):
            self.assertTrue(self.vmctl.tool_present("virtiofsd"))
            self.assertTrue(self.vmctl.tool_present("7z"))
            self.assertFalse(self.vmctl.tool_present("dialog"))

    def test_textual_python_follows_the_vmtui_lookup_order(self):
        venv_python = str(self.root / ".venv-tui/bin/python")

        def probe(returncodes):
            def fake_run(cmd, **kwargs):
                if cmd[0] not in returncodes:
                    raise FileNotFoundError(cmd[0])
                return subprocess.CompletedProcess(cmd, returncodes[cmd[0]])
            return fake_run

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VMTUI_TEXTUAL_PYTHON", None)
            with mock.patch.object(subprocess, "run", side_effect=probe({venv_python: 0, "python3": 0})):
                self.assertEqual(self.vmctl.textual_python(), venv_python)
            with mock.patch.object(subprocess, "run", side_effect=probe({"python3": 0})):
                self.assertEqual(self.vmctl.textual_python(), "python3")
            with mock.patch.object(subprocess, "run", side_effect=probe({venv_python: 1, "python3": 1})):
                self.assertIsNone(self.vmctl.textual_python())
                self.assertFalse(self.vmctl.tool_present("textual"))
            with mock.patch.dict(os.environ, {"VMTUI_TEXTUAL_PYTHON": "/opt/py"}), \
                 mock.patch.object(subprocess, "run", side_effect=probe({"/opt/py": 1, "python3": 0})):
                self.assertIsNone(self.vmctl.textual_python())

    def test_cmd_setup_reports_missing_textual(self):
        self.write_config_dir()
        with mock.patch.object(shutil, "which", return_value="/usr/bin/fake"), \
             mock.patch.object(vmctl.host_setup, "textual_python", return_value=None), \
             mock.patch("sys.stdout", new_callable=mock.MagicMock()) as stdout:
            exit_code = self.vmctl.cmd_setup(argparse.Namespace())

        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertEqual(exit_code, 0)
        self.assertIn("[missing] textual (the vmtui dashboard", output)
        self.assertIn("make install textual", output)

    def _install(self, names, present=(), distro="ubuntu", apt_has=None, ensurepip=False, **kwargs):
        with mock.patch.object(vmctl.host_setup, "tool_present", side_effect=lambda name: name in present), \
             mock.patch.object(vmctl.host_setup, "apt_available", return_value=apt_has), \
             mock.patch.object(vmctl.host_setup, "python_has_ensurepip", return_value=ensurepip), \
             mock.patch.object(vmctl.host_setup, "read_os_release", return_value={"ID": distro}), \
             mock.patch.object(vmctl.runtime, "confirm_default_no", return_value=True) as confirm, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            vmctl.host_setup.install_tools(names, **kwargs)
        return [call.args[0] for call in run_cmd.call_args_list], confirm, stdout.getvalue()

    def test_setup_install_named_tools_maps_them_to_packages_once(self):
        executed, confirm, output = self._install(["growisofs", "cloud-localds", "ddrescue", "ddrescuelog", "fzf"], present={"fzf"})
        self.assertEqual(executed, [["sudo", "apt", "update"],
                                    ["sudo", "env", "NEEDRESTART_MODE=l", "apt", "install", "-y", "dvd+rw-tools", "cloud-image-utils", "gddrescue"]])
        self.assertIn("fzf is already installed", output)
        confirm.assert_called_once()

    def test_setup_install_skips_packages_apt_does_not_have(self):
        # Ubuntu 22.04: one unknown package name made `apt install` refuse the whole list
        # (reported from a Lubuntu host, 2026-09-26).
        executed, _, output = self._install(["xorriso", "swtpm"], apt_has={"xorriso"})
        self.assertEqual(executed[-1], ["sudo", "env", "NEEDRESTART_MODE=l", "apt", "install", "-y", "xorriso"])
        self.assertIn("swtpm: no apt package on this release, skipped", output)

    def test_setup_install_brings_the_upstream_virtiofsd_where_apt_has_none(self):
        # Ubuntu 22.04 has no virtiofsd package, only QEMU's C daemon that needs root.
        with mock.patch.object(vmctl.host_setup, "install_upstream_virtiofsd") as upstream:
            executed, _, output = self._install(["xorriso", "virtiofsd"], apt_has={"xorriso"})
        upstream.assert_called_once()
        self.assertEqual(executed[-1], ["sudo", "env", "NEEDRESTART_MODE=l", "apt", "install", "-y", "xorriso"])
        self.assertIn("virtiofsd <- upstream static build", output)
        with mock.patch.object(vmctl.host_setup, "install_upstream_virtiofsd") as upstream:
            executed, _, _ = self._install(["virtiofsd"], apt_has={"virtiofsd"})
        upstream.assert_not_called()
        self.assertEqual(executed[-1], ["sudo", "env", "NEEDRESTART_MODE=l", "apt", "install", "-y", "virtiofsd"])

    def test_legacy_c_virtiofsd_is_not_used(self):
        def fake_run(cmd, **kwargs):
            if cmd[0].startswith("/usr/lib/qemu"):  # what the C daemon says to a user that is not root
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="setgroups() failed with error=1:Operation not permitted\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="virtiofsd 1.14.0\n", stderr="")
        which = {"/usr/lib/qemu/virtiofsd": "/usr/lib/qemu/virtiofsd"}
        with mock.patch.object(shutil, "which", side_effect=lambda name: which.get(name)), \
             mock.patch.object(vmctl.qemu.subprocess, "run", side_effect=fake_run):
            self.assertIsNone(vmctl.qemu.find_virtiofsd())
            local = str(self.root / ".tools/virtiofsd")
            which[local] = local
            self.assertEqual(vmctl.qemu.find_virtiofsd(), local)

    def test_setup_install_textual_on_apt_brings_python3_venv(self):
        venv = self.root / ".venv-tui"
        executed, _, _ = self._install(["textual"])
        self.assertEqual(executed[:3], [["sudo", "apt", "update"], ["sudo", "env", "NEEDRESTART_MODE=l", "apt", "install", "-y", "python3-venv"],
                                        ["python3", "-m", "venv", str(venv)]])

    def test_apt_available_reads_the_candidates_of_apt_cache_policy(self):
        policy = ("xorriso:\n  Installed: (none)\n  Candidate: 1.5.4-2\n  Version table:\n"
                  "swtpm:\n  Installed: (none)\n  Candidate: (none)\n")
        with mock.patch.object(shutil, "which", return_value="/usr/bin/apt-cache"), \
             mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=policy)):
            self.assertEqual(vmctl.host_setup.apt_available(["xorriso", "swtpm", "virtiofsd"]), {"xorriso"})
        with mock.patch.object(shutil, "which", return_value=None):
            self.assertIsNone(vmctl.host_setup.apt_available(["xorriso"]))

    def test_apt_available_asks_in_english_and_never_filters_everything(self):
        # An Italian host prints "Candidato:": every package looked missing and none was installed.
        translated = "xorriso:\n  Installato: (nessuno)\n  Candidato: 1.5.4-2\n"
        with mock.patch.object(shutil, "which", return_value="/usr/bin/apt-cache"), \
             mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=translated)) as run:
            self.assertIsNone(vmctl.host_setup.apt_available(["xorriso"]))
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_setup_install_without_names_installs_every_missing_tool_with_pacman(self):
        present = set(vmctl.host_setup.installable_names()) - {"sfdisk", "7z"}
        executed, _, _ = self._install([], present=present, distro="arch")
        self.assertEqual(executed, [["sudo", "pacman", "-S", "--needed", "p7zip", "util-linux"]])

    def test_setup_install_textual_goes_into_the_repo_venv_without_sudo(self):
        venv = self.root / ".venv-tui"
        executed, _, _ = self._install(["textual"], distro="unknown")
        self.assertEqual(executed, [["python3", "-m", "venv", str(venv)],
                                    [str(venv / "bin/python"), "-m", "pip", "install", "--quiet", "-e", f"{self.root}[tui]"]])
        (venv / "bin").mkdir(parents=True)
        (venv / "bin/python").write_text("", encoding="utf-8")
        with mock.patch.object(vmctl.host_setup, "textual_venv_usable", return_value=True):
            executed, _, _ = self._install(["textual"])
        self.assertEqual([cmd[1:3] for cmd in executed], [["-m", "pip"]])

    def test_setup_install_skips_python3_venv_when_ensurepip_is_there(self):
        # python3-venv already installed: no sudo just to be told it is there (Lubuntu 22.04).
        executed, _, _ = self._install(["textual"], ensurepip=True)
        self.assertEqual([cmd[:3] for cmd in executed][0], ["python3", "-m", "venv"])

    def test_setup_install_rebuilds_a_venv_left_without_pip(self):
        # A venv created while python3-venv was missing has a python but no pip; reusing it
        # ended in "No module named pip" (Lubuntu 22.04).
        venv = self.root / ".venv-tui"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin/python").write_text("", encoding="utf-8")
        with mock.patch.object(vmctl.host_setup, "textual_venv_usable", return_value=False):
            executed, _, _ = self._install(["textual"])
        self.assertEqual(executed[1], ["sudo", "env", "NEEDRESTART_MODE=l", "apt", "install", "-y", "python3-venv"])
        self.assertEqual(executed[2], ["python3", "-m", "venv", "--clear", str(venv)])
        self.assertEqual(executed[3][1:4], ["-m", "pip", "install"])

    def test_setup_install_refuses_unknown_names_unconfirmed_runs_and_unknown_distros(self):
        with self.assertRaisesRegex(self.vmctl.VMError, "Unknown tool\\(s\\): nope. Installable: qemu-system-x86_64"):
            self._install(["nope"])
        with mock.patch.object(vmctl.runtime, "confirm_default_no", return_value=False), \
             mock.patch.object(vmctl.host_setup, "tool_present", return_value=False), \
             mock.patch.object(vmctl.host_setup, "read_os_release", return_value={"ID": "debian"}), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaisesRegex(self.vmctl.VMError, "Not confirmed"):
                vmctl.host_setup.install_tools(["xorriso"])
        run_cmd.assert_not_called()
        with self.assertRaisesRegex(self.vmctl.VMError, "No package list for this distribution; install xorriso"):
            self._install(["xorriso"], distro="gentoo")

    def test_setup_install_yes_and_dry_run_skip_the_question(self):
        _, confirm, _ = self._install(["xorriso"], assume_yes=True)
        confirm.assert_not_called()
        executed, confirm, _ = self._install(["xorriso"], dry_run=True)
        confirm.assert_not_called()
        self.assertEqual(executed[-1], ["sudo", "env", "NEEDRESTART_MODE=l", "apt", "install", "-y", "xorriso"])

    def test_setup_install_names_everything_setup_checks(self):
        names = set(vmctl.host_setup.installable_names())
        self.assertLessEqual(set(vmctl.state.REQUIRED_COMMANDS) | set(vmctl.state.OPTIONAL_COMMANDS), names)

    def test_setup_groups_cover_every_checked_tool_exactly_once(self):
        grouped = [name for _, names in vmctl.host_setup.SETUP_GROUPS for name in names]
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual(set(grouped), set(vmctl.state.REQUIRED_COMMANDS) | set(vmctl.state.OPTIONAL_COMMANDS))

    def test_setup_prints_one_line_per_group_and_the_missing_tool_with_its_package(self):
        self.write_config_dir()
        with mock.patch.object(vmctl.host_setup, "tool_present", side_effect=lambda name: name != "growisofs"), \
             mock.patch.object(vmctl.host_setup, "textual_python", return_value=str(self.root / ".venv-tui/bin/python")), \
             mock.patch.object(vmctl.host_setup, "read_os_release", return_value={"ID": "ubuntu"}), \
             mock.patch.object(vmctl.host_setup, "kvm_status", return_value=(True, "/dev/kvm")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(self.vmctl.cmd_setup(argparse.Namespace()), 0)
        output = stdout.getvalue()
        self.assertIn("textual (.venv-tui) · fzf · dialog", output)
        self.assertIn("partclone.{extfs,ntfs,fat,exfat}", output)
        self.assertIn("[missing] growisofs (bootstrap-pfsense and bootstrap-freebsd (updates the ISO in place); dvd+rw-tools package)", output)
        self.assertIn("make setup installs every missing one (one only: make install growisofs)", output)
        self.assertEqual(sum("[ok]" in line for line in output.splitlines()), len(vmctl.host_setup.SETUP_GROUPS) + 3)

    def test_kvm_status_explains_a_missing_or_unwritable_device(self):
        with mock.patch.object(Path, "exists", return_value=False):
            self.assertFalse(vmctl.host_setup.kvm_status()[0])
        with mock.patch.object(Path, "exists", return_value=True), mock.patch.object(os, "access", return_value=False):
            ok, detail = vmctl.host_setup.kvm_status()
            self.assertFalse(ok)
            self.assertIn("kvm group", detail)

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
            with mock.patch.object(vmctl.host_setup, "textual_python", return_value="python3"):
                exit_code = self.vmctl.cmd_setup(args)

        self.assertEqual(exit_code, 0)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0], ["sudo", "apt", "update"])
        self.assertEqual(
            executed[1],
            ["sudo", "env", "NEEDRESTART_MODE=l", "apt", "install", "-y", "qemu-system-x86", "qemu-utils", "ovmf", "python3", "openssh-client", "libvirt-clients", "libvirt-daemon-system", "make", "dialog", "fzf", "cloud-image-utils", "xorriso", "virtiofsd", "virt-viewer", "p7zip-full", "dvd+rw-tools", "python3-bcrypt", "swtpm", "gdisk", "gddrescue", "partclone", "fdisk"],
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
            with mock.patch.object(vmctl.host_setup, "textual_python", return_value="python3"):
                exit_code = self.vmctl.cmd_setup(args)

        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertEqual(exit_code, 0)
        self.assertIn("Setup check passed.", output)


if __name__ == "__main__":
    unittest.main()
