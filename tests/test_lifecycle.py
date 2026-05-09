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


class VmctlTests(BaseVmctlTestCase):
    def test_cmd_list_includes_local_profile_override_file(self):
        local_vm = json.loads(json.dumps(self.vm_config))
        local_vm["name"] = "Local VM"
        local_vm["disk"]["path"] = "artifacts/localvm/disk.qcow2"
        self.write_extra_profile("local.json", {"vms": {"localvm": local_vm}})

        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_list(argparse.Namespace())

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("localvm", output)
        self.assertIn("Local VM", output)

    def test_cmd_status_reports_disk_iso_and_nvram_state(self):
        disk_path = self.create_disk()
        iso_path = self.root / self.vm_config["iso"]
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        iso_path.write_text("iso", encoding="utf-8")
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        vars_path = self.root / self.vm_config["firmware"]["vars_path"]
        vars_path.parent.mkdir(parents=True, exist_ok=True)
        vars_path.write_text("vars", encoding="utf-8")
        args = argparse.Namespace(all=False)

        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-img"), \
             mock.patch.object(vmctl.runtime, "image_info", return_value={"virtual-size": 2 * 1024**3}), \
             mock.patch.object(vmctl.lifecycle, "vm_runtime_status", return_value=("-", "-")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_status(args)

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn(self.vm_name, output)
        self.assertIn("ready", output)
        self.assertIn(self.vmctl.format_bytes(disk_path.stat().st_size), output)
        self.assertIn(self.vmctl.format_bytes(2 * 1024**3), output)
        self.assertIn("RUNTIME", output)

    def test_cmd_status_hides_untouched_vms_by_default(self):
        other_vm = json.loads(json.dumps(self.vm_config))
        other_vm["disk"]["path"] = "artifacts/othervm/disk.qcow2"
        self.create_disk()
        (self.config_dir / "profiles" / "other.json").write_text(
            json.dumps({"vms": {"othervm": other_vm}}, indent=2) + "\n",
            encoding="utf-8",
        )

        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_status(argparse.Namespace(all=False))

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn(self.vm_name, output)
        self.assertNotIn("othervm", output)

    def test_cmd_status_reports_runtime_port_and_note(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.write_config_dir()

        with mock.patch.object(vmctl.lifecycle, "vm_runtime_status", return_value=("hostfwd:2222", "pid=4242")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_status(argparse.Namespace(all=False))

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("hostfwd:2222 (pid=4242)", output)
        self.assertNotIn("\nnote", output)

    def test_cmd_status_cleans_stale_bootstrap_pid_file(self):
        pid_path = self.root / "artifacts/testvm/runtime/bootstrap-start.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("424242\n", encoding="utf-8")
        self.create_disk()
        self.write_config_dir()

        with mock.patch.object(vmctl.lifecycle, "find_qemu_process_by_disk_path", return_value=(None, None)), \
             mock.patch.object(vmctl.lifecycle, "local_tcp_port_open", return_value=False), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            exit_code = self.vmctl.cmd_status(argparse.Namespace(all=False))

        self.assertEqual(exit_code, 0)
        self.assertFalse(pid_path.exists())

    def test_status_cell_style_colors_ready_missing_and_runtime_states(self):
        self.assertEqual(vmctl.lifecycle.status_cell_style("ready"), (vmctl.ui.GREEN, vmctl.ui.BOLD))
        self.assertEqual(vmctl.lifecycle.status_cell_style("missing"), (vmctl.ui.YELLOW, vmctl.ui.BOLD))
        self.assertEqual(vmctl.lifecycle.status_cell_style("hostfwd:2222"), (vmctl.ui.GREEN, vmctl.ui.BOLD))
        self.assertEqual(vmctl.lifecycle.status_cell_style("closed:2222"), (vmctl.ui.YELLOW, vmctl.ui.BOLD))

    def test_vm_runtime_status_prefers_disk_identity_over_shared_port(self):
        vm = json.loads(json.dumps(self.vm_config))
        vm["disk"]["path"] = "artifacts/testvm/disk.qcow2"
        vm["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}

        with mock.patch.object(vmctl.lifecycle, "is_bootstrap_vm_running", return_value=(False, None, None)), \
             mock.patch.object(
                 vmctl.lifecycle,
                 "find_qemu_process_by_disk_path",
                 return_value=(4242, f"qemu-system-x86_64 -drive file={self.root / vm['disk']['path']},format=qcow2,if=virtio -netdev user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22"),
             ):
            runtime_str, runtime_note = vmctl.lifecycle.vm_runtime_status("testvm", vm)

        self.assertEqual(runtime_str, "hostfwd:2222")
        self.assertEqual(runtime_note, "pid=4242")

    def test_vm_runtime_status_does_not_claim_shared_port_without_matching_disk(self):
        vm = json.loads(json.dumps(self.vm_config))
        vm["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}

        with mock.patch.object(vmctl.lifecycle, "is_bootstrap_vm_running", return_value=(False, None, None)), \
             mock.patch.object(vmctl.lifecycle, "find_qemu_process_by_disk_path", return_value=(None, None)):
            runtime_str, runtime_note = vmctl.lifecycle.vm_runtime_status("testvm", vm)

        self.assertEqual(runtime_str, "closed:2222")
        self.assertEqual(runtime_note, "-")

    def test_cmd_status_handles_locked_disk_image_quietly(self):
        self.create_disk()
        self.write_config_dir()

        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-img"), \
             mock.patch.object(vmctl.runtime, "image_info",
                 side_effect=self.vmctl.subprocess.CalledProcessError(
                     1,
                     ["qemu-img", "info"],
                     stderr='qemu-img: Failed to get shared "write" lock',
                 ),
             ), \
             mock.patch.object(vmctl.lifecycle, "vm_runtime_status", return_value=("-", "-")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_status(argparse.Namespace(all=False))

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn(self.vm_name, output)
        self.assertIn("?", output)
        self.assertNotIn("Failed to get shared", output)

    def test_cmd_list_emits_json_when_flag_set(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_list(argparse.Namespace(json=True))

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(len(payload), 1)
        entry = payload[0]
        self.assertEqual(entry["profile"], self.vm_name)
        self.assertEqual(entry["name"], self.vm_config["name"])
        self.assertEqual(entry["memory_mb"], self.vm_config["memory_mb"])
        self.assertEqual(entry["cpus"], self.vm_config["cpus"])

    def test_cmd_status_emits_json_when_flag_set(self):
        self.create_disk()
        self.write_config_dir()

        with mock.patch.object(vmctl.lifecycle, "vm_runtime_status", return_value=("-", "-")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_status(argparse.Namespace(all=False, json=True))

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(len(payload), 1)
        entry = payload[0]
        self.assertEqual(entry["profile"], self.vm_name)
        self.assertEqual(entry["disk"], "ready")
        self.assertIsNone(entry["runtime_note"])

    def test_cmd_show_skips_header_in_json_mode(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_show(argparse.Namespace(vm=self.vm_name, json=True))

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertNotIn("==>", output)
        payload = json.loads(output)
        self.assertEqual(payload["name"], self.vm_config["name"])

    def test_cmd_prep_downloads_iso_when_missing(self):
        args = argparse.Namespace(vm=self.vm_name, dry_run=False)

        with mock.patch.object(vmctl.iso, "download_file") as download_file, \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_prep(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once()
        run_cmd.assert_called_once()
        qemu_img_cmd = run_cmd.call_args.args[0]
        self.assertEqual(qemu_img_cmd[:3], ["qemu-img", "create", "-f"])

    def test_cmd_provision_dry_run_prepares_and_starts_installer(self):
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, video="std", no_start=False, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file") as download_file, \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_provision(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)
        self.assertEqual(run_cmd.call_count, 2)
        qemu_img_cmd = run_cmd.call_args_list[0].args[0]
        qemu_cmd = run_cmd.call_args_list[1].args[0]
        self.assertEqual(qemu_img_cmd[:3], ["qemu-img", "create", "-f"])
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn("-cdrom", qemu_cmd)
        self.assertEqual(qemu_cmd[qemu_cmd.index("-cdrom") + 1], str(iso_path))
        self.assertIn(f"file={self.root / self.vm_config['disk']['path']},format=qcow2,if=virtio", qemu_cmd)

    def test_cmd_provision_no_start_only_prepares_artifacts(self):
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, video=None, no_start=True, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file") as download_file, \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_provision(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)
        run_cmd.assert_called_once()
        self.assertEqual(run_cmd.call_args.args[0][:3], ["qemu-img", "create", "-f"])

    def test_cmd_install_dry_run_builds_qemu_command_with_cdrom(self):
        disk_path = self.create_disk()
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, video="std", cloud_init=False, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file") as download_file, \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)
        run_cmd.assert_called_once()
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertEqual(run_cmd.call_args.kwargs["dry_run"], True)
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn(f"file={disk_path},format=qcow2,if=virtio", qemu_cmd)
        self.assertIn("-cdrom", qemu_cmd)
        self.assertEqual(qemu_cmd[qemu_cmd.index("-cdrom") + 1], str(iso_path))
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)

    def test_cmd_install_defaults_to_std_video_for_non_ubuntu_installer(self):
        self.create_disk()
        self.vm_config["video"]["default"] = "virtio-gl"
        self.vm_config["video"]["variants"]["virtio-gl"] = ["-device", "virtio-vga-gl", "-display", "gtk,gl=on"]
        self.vm_config["video"]["variants"]["safe"] = ["-vga", "std", "-display", "gtk", "-serial", "mon:stdio"]
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file"), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)
        self.assertNotIn("-serial", qemu_cmd)
        self.assertNotIn("virtio-vga-gl", qemu_cmd)

    def test_cmd_install_prefers_std_over_safe_by_default(self):
        self.create_disk()
        self.vm_config["video"]["variants"]["safe"] = ["-vga", "std", "-display", "gtk", "-serial", "mon:stdio"]
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file"), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertNotIn("-serial", qemu_cmd)

    def test_cmd_install_honours_installer_order_when_set(self):
        self.create_disk()
        self.vm_config["video"]["default"] = "virtio-gl"
        self.vm_config["video"]["installer_order"] = ["std", "safe"]
        self.vm_config["video"]["variants"]["virtio-gl"] = ["-device", "virtio-vga-gl", "-display", "gtk,gl=on"]
        self.vm_config["video"]["variants"]["safe"] = ["-vga", "std", "-display", "gtk", "-serial", "mon:stdio"]
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file"), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)
        self.assertNotIn("-serial", qemu_cmd)
        self.assertNotIn("virtio-vga-gl", qemu_cmd)

    def test_ensure_vm_dirs_wraps_permission_errors(self):
        with mock.patch.object(pathlib.Path, "mkdir", side_effect=PermissionError("denied")):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.ensure_vm_dirs(self.vm_name)

    def test_cmd_start_dry_run_builds_qemu_command_without_cdrom(self):
        disk_path = self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, headless=False, background=False, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        run_cmd.assert_called_once()
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertEqual(run_cmd.call_args.kwargs["dry_run"], True)
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn(f"file={disk_path},format=qcow2,if=virtio", qemu_cmd)
        self.assertNotIn("-cdrom", qemu_cmd)
        self.assertIn("-netdev", qemu_cmd)
        self.assertIn("user,id=n1", qemu_cmd)

    def test_cmd_start_headless_dry_run_uses_no_display(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video="std", cloud_init=False, headless=True, background=False, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertIn("-monitor", qemu_cmd)
        self.assertNotIn("-vga", qemu_cmd)

    def test_cmd_start_headless_background_tracks_pid_and_log(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, headless=True, background=True, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None) as run_background:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_background.call_args.args[0]
        log_path = run_background.call_args.args[1]
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertIn("-chardev", qemu_cmd)
        self.assertIn("stdio,id=char0,signal=off", qemu_cmd)
        self.assertIn("-serial", qemu_cmd)
        self.assertIn("chardev:char0", qemu_cmd)
        self.assertEqual(log_path, self.root / "artifacts/testvm/logs/bootstrap-start.log")

    def test_cmd_start_spice_background_tracks_pid_and_log(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, headless=False, background=True, spice_port=5930, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None) as run_background:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_background.call_args.args[0]
        self.assertIn("-spice", qemu_cmd)
        self.assertIn("addr=127.0.0.1,port=5930,disable-ticketing=on", qemu_cmd)
        self.assertIn("virtserialport,chardev=vdagent0,name=com.redhat.spice.0", qemu_cmd)
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)

    def test_cmd_start_background_requires_headless(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, headless=False, background=True, dry_run=True)

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_start(args)

    def test_cmd_start_aborts_if_background_vm_already_running(self):
        self.create_disk()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=False, headless=False, background=False, dry_run=True)

        with mock.patch.object(vmctl.lifecycle, "is_bootstrap_vm_running", return_value=(True, 1234, "qemu-system-x86_64")), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 1)
        run_cmd.assert_not_called()

    def test_common_args_tcg_headless_serial(self):
        disk_path = self.create_disk()

        with mock.patch.object(vmctl.runtime, "require_command"):
            qemu_cmd = self.vmctl.common_args(
                self.vm_config,
                variant=None,
                dry_run=True,
                accel="tcg",
                headless=True,
                serial_stdio=True,
                no_reboot=True,
            )

        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertNotIn("-enable-kvm", qemu_cmd)
        self.assertIn("pc,accel=tcg", qemu_cmd)
        self.assertIn(f"file={disk_path},format=qcow2,if=virtio", qemu_cmd)
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertIn("-serial", qemu_cmd)
        self.assertIn("-no-reboot", qemu_cmd)

    def test_common_args_honors_custom_network_device(self):
        self.create_disk()
        self.vm_config["network_device"] = "e1000e"

        with mock.patch.object(vmctl.runtime, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant=None, dry_run=True)

        self.assertIn("e1000e,netdev=n1", qemu_cmd)

    def test_common_args_adds_hostfwd_for_cloud_init_ssh(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}

        with mock.patch.object(vmctl.runtime, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant=None, dry_run=True)

        self.assertIn("user,id=n1,hostfwd=tcp:127.0.0.1:2222-:22", qemu_cmd)

    def test_common_args_adds_hostfwd_for_ssh_provision(self):
        self.create_disk()
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2223}

        with mock.patch.object(vmctl.runtime, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant=None, dry_run=True)

        self.assertIn("user,id=n1,hostfwd=tcp:127.0.0.1:2223-:22", qemu_cmd)

    def test_common_args_supports_sata_disk_interface(self):
        disk_path = self.create_disk()
        self.vm_config["disk"]["interface"] = "sata"

        with mock.patch.object(vmctl.runtime, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant=None, dry_run=True)

        self.assertIn("ich9-ahci,id=ahci0", qemu_cmd)
        self.assertIn(f"id=disk0,file={disk_path},format=qcow2,if=none", qemu_cmd)
        self.assertIn("ide-hd,drive=disk0,bus=ahci0.0", qemu_cmd)

    def test_common_args_enables_clipboard_agent_for_graphical_vm(self):
        self.create_disk()
        self.vm_config["clipboard"] = True

        with mock.patch.object(vmctl.runtime, "require_command"):
            qemu_cmd = self.vmctl.common_args(self.vm_config, variant="std", dry_run=True)

        self.assertIn("virtio-serial-pci", qemu_cmd)
        self.assertIn("qemu-vdagent,id=vdagent0,name=vdagent,clipboard=on", qemu_cmd)
        self.assertIn("virtserialport,chardev=vdagent0,name=com.redhat.spice.0", qemu_cmd)

    def test_common_args_can_disable_clipboard_agent(self):
        self.create_disk()
        self.vm_config["clipboard"] = True

        with mock.patch.object(vmctl.runtime, "require_command"):
            qemu_cmd = self.vmctl.common_args(
                self.vm_config,
                variant="std",
                dry_run=True,
                enable_clipboard=False,
            )

        self.assertNotIn("virtio-serial-pci", qemu_cmd)
        self.assertNotIn("qemu-vdagent,id=vdagent0,name=vdagent,clipboard=on", qemu_cmd)
        self.assertNotIn("virtserialport,chardev=vdagent0,name=com.redhat.spice.0", qemu_cmd)

    def test_cmd_boot_check_dry_run_uses_ci_settings(self):
        self.create_disk()
        self.vm_config["ci"] = {
            "accel": "tcg",
            "headless": True,
            "boot_from": "cdrom",
            "auto_input": [{"match": "boot:", "send": "\r"}, {"match": "boot:", "send": "\n"}],
            "expect": "login:",
            "timeout_sec": 180,
        }
        self.write_config_dir()
        iso_path = self.root / self.vm_config["iso"]
        args = argparse.Namespace(vm=self.vm_name, expect=None, timeout=None, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file") as download_file, \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.qemu, "run_and_expect") as run_and_expect:
            exit_code = self.vmctl.cmd_boot_check(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)
        run_and_expect.assert_called_once()
        qemu_cmd, expected_text, timeout_sec = run_and_expect.call_args.args
        self.assertIn("-cdrom", qemu_cmd)
        self.assertEqual(qemu_cmd[qemu_cmd.index("-cdrom") + 1], str(iso_path))
        self.assertEqual(expected_text, "login:")
        self.assertEqual(timeout_sec, 180)
        self.assertEqual(
            run_and_expect.call_args.kwargs["auto_inputs"],
            [("boot:", "\r"), ("boot:", "\n")],
        )

    def test_cmd_boot_check_disk_mode_skips_cdrom_and_uses_existing_disk(self):
        self.create_disk()
        self.vm_config["ci"] = {
            "accel": "tcg",
            "headless": True,
            "boot_from": "disk",
            "expect": "login:",
            "timeout_sec": 120,
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, expect=None, timeout=None, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file") as download_file, \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.qemu, "run_and_expect") as run_and_expect:
            exit_code = self.vmctl.cmd_boot_check(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_not_called()
        qemu_cmd, expected_text, timeout_sec = run_and_expect.call_args.args
        self.assertNotIn("-cdrom", qemu_cmd)
        self.assertEqual(expected_text, "login:")
        self.assertEqual(timeout_sec, 120)

    def test_local_test_mode_prefers_bootstrap_bootcheck_and_skip(self):
        unattended_vm = json.loads(json.dumps(self.vm_config))
        unattended_vm["autoinstall"] = {"username": "vmuser", "password_hash": "hash"}
        unattended_vm["cloud_init"] = {"user": "vmuser", "ssh_host_port": 2222}

        boot_vm = json.loads(json.dumps(self.vm_config))
        boot_vm["ci"] = {"expect": "login:"}

        skipped_vm = json.loads(json.dumps(self.vm_config))

        template_vm = json.loads(json.dumps(self.vm_config))
        template_vm["meta"] = {"role": "import-template"}

        self.assertEqual(
            vmctl.lifecycle.local_test_mode(unattended_vm),
            ("bootstrap-unattended", "autoinstall + post-install"),
        )
        self.assertEqual(
            vmctl.lifecycle.local_test_mode(boot_vm),
            ("boot-check", "serial boot expectation"),
        )
        self.assertEqual(
            vmctl.lifecycle.local_test_mode(skipped_vm),
            ("skip", "missing ci.expect for boot-check"),
        )
        self.assertEqual(
            vmctl.lifecycle.local_test_mode(template_vm),
            ("skip", "import-template profile"),
        )

    def test_prepare_vm_for_local_test_reassigns_busy_ssh_port(self):
        vm = json.loads(json.dumps(self.vm_config))
        vm["cloud_init"] = {"user": "vmuser", "ssh_host_port": 2222}

        with mock.patch.object(vmctl.lifecycle, "find_qemu_process_by_disk_path", return_value=(None, None)), \
             mock.patch.object(vmctl.lifecycle, "local_tcp_port_open", return_value=True), \
             mock.patch.object(vmctl.lifecycle, "pick_free_local_port", return_value=2299):
            prepared, note = vmctl.lifecycle.prepare_vm_for_local_test("testvm", vm)

        self.assertEqual(vm["cloud_init"]["ssh_host_port"], 2222)
        self.assertEqual(prepared["cloud_init"]["ssh_host_port"], 2299)
        self.assertIn("2222", note)
        self.assertIn("2299", note)

    def test_prepare_vm_for_local_test_skips_when_disk_is_in_use(self):
        vm = json.loads(json.dumps(self.vm_config))

        with mock.patch.object(vmctl.lifecycle, "find_qemu_process_by_disk_path", return_value=(4242, "qemu-system-x86_64 ...")):
            prepared, note = vmctl.lifecycle.prepare_vm_for_local_test("testvm", vm)

        self.assertEqual(prepared["disk"]["path"], vm["disk"]["path"])
        self.assertEqual(note, "disk already in use by qemu pid 4242")

    def test_run_local_test_vm_skips_uninitialized_disk_boot_check(self):
        vm = json.loads(json.dumps(self.vm_config))
        vm["ci"] = {"boot_from": "disk", "expect": "login:"}
        self.write_config_dir()
        self.create_disk()
        args = argparse.Namespace(timeout=300, dry_run=True)

        with mock.patch.object(vmctl.lifecycle, "find_qemu_process_by_disk_path", return_value=(None, None)), \
             mock.patch.object(vmctl.lifecycle, "cmd_boot_check") as boot_check:
            status, detail = vmctl.lifecycle.run_local_test_vm("testvm", vm, args)

        self.assertEqual(status, "skipped")
        self.assertIn("looks uninitialized", detail)
        boot_check.assert_not_called()

    def test_local_test_clean_candidates_selects_bootstrap_profiles_only(self):
        ubuntu_vm = json.loads(json.dumps(self.vm_config))
        ubuntu_vm["autoinstall"] = {"username": "vmuser", "password_hash": "hash"}
        ubuntu_vm["cloud_init"] = {"user": "vmuser", "ssh_host_port": 2222}

        arch_vm = json.loads(json.dumps(self.vm_config))
        arch_vm["archinstall_config"] = {"username": "vmuser", "password": "pw"}
        arch_vm["cloud_init"] = {"user": "vmuser", "ssh_host_port": 2223}

        boot_vm = json.loads(json.dumps(self.vm_config))
        boot_vm["ci"] = {"expect": "login:"}

        self.write_extra_profile(
            "clean-candidates.json",
            {"vms": {"ubuntu": ubuntu_vm, "arch": arch_vm, "boot": boot_vm}},
        )
        cfg = self.vmctl.config.load_config()

        candidates = vmctl.lifecycle.local_test_clean_candidates(["ubuntu", "arch", "boot"], cfg)

        self.assertEqual(candidates, ["ubuntu", "arch"])

    def test_cmd_test_local_cleans_bootstrap_profiles_before_running(self):
        ubuntu_vm = json.loads(json.dumps(self.vm_config))
        ubuntu_vm["autoinstall"] = {"username": "vmuser", "password_hash": "hash"}
        ubuntu_vm["cloud_init"] = {"user": "vmuser", "ssh_host_port": 2222}
        self.write_extra_profile("clean-before-run.json", {"vms": {"ubuntu": ubuntu_vm}})
        args = argparse.Namespace(vms=["ubuntu"], timeout=300, parallel=1, dry_run=True, clean_first=False, no_clean_first=False)

        with mock.patch.object(vmctl.host_setup, "prompt_yes_no_default_yes", return_value=True), \
             mock.patch.object(vmctl.lifecycle, "cmd_bootstrap_unattended") as bootstrap_unattended, \
             mock.patch.object(vmctl.lifecycle, "cmd_stop") as cmd_stop, \
             mock.patch.object(vmctl.lifecycle, "clean_vm") as clean_vm:
            exit_code = self.vmctl.cmd_test_local(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(cmd_stop.call_count, 2)
        self.assertTrue(all(call.args[0].vm == "ubuntu" for call in cmd_stop.call_args_list))
        clean_vm.assert_called_once()
        self.assertEqual(clean_vm.call_args.args[0], "ubuntu")
        bootstrap_unattended.assert_called_once()

    def test_cmd_test_local_runs_matrix_and_summarizes_results(self):
        ubuntu_vm = json.loads(json.dumps(self.vm_config))
        ubuntu_vm["disk"]["path"] = "artifacts/ubuntu/disk.qcow2"
        ubuntu_vm["autoinstall"] = {"username": "vmuser", "password_hash": "hash"}
        ubuntu_vm["cloud_init"] = {"user": "vmuser", "ssh_host_port": 2222}

        boot_vm = json.loads(json.dumps(self.vm_config))
        boot_vm["disk"]["path"] = "artifacts/alpine/disk.qcow2"
        boot_vm["ci"] = {"expect": "login:"}

        skipped_vm = json.loads(json.dumps(self.vm_config))
        skipped_vm["disk"]["path"] = "artifacts/manual/disk.qcow2"

        template_vm = json.loads(json.dumps(self.vm_config))
        template_vm["disk"]["path"] = "artifacts/template/disk.qcow2"
        template_vm["meta"] = {"role": "import-template"}

        self.write_extra_profile(
            "matrix.json",
            {
                "vms": {
                    "ubuntu": ubuntu_vm,
                    "alpine": boot_vm,
                    "manual": skipped_vm,
                    "template": template_vm,
                }
            },
        )

        args = argparse.Namespace(vms=[], timeout=300, dry_run=True, clean_first=False, no_clean_first=False)

        with mock.patch.object(vmctl.host_setup, "prompt_yes_no_default_yes", return_value=False), \
             mock.patch.object(vmctl.lifecycle, "cmd_bootstrap_unattended") as bootstrap_unattended, \
             mock.patch.object(vmctl.lifecycle, "cmd_boot_check") as boot_check, \
             mock.patch.object(vmctl.lifecycle, "cmd_stop") as cmd_stop, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_test_local(args)

        self.assertEqual(exit_code, 0)
        bootstrap_unattended.assert_called_once()
        self.assertEqual(bootstrap_unattended.call_args.args[0].vm, "ubuntu")
        cmd_stop.assert_called_once()
        self.assertEqual(cmd_stop.call_args.args[0].vm, "ubuntu")
        booted = sorted(call.args[0].vm for call in boot_check.call_args_list)
        self.assertEqual(booted, ["alpine"])
        output = stdout.getvalue()
        self.assertIn("passed", output)
        self.assertIn("skipped", output)
        self.assertIn("ubuntu", output)
        self.assertIn("manual", output)

    def test_cmd_check_vm_prints_result_marker(self):
        boot_vm = json.loads(json.dumps(self.vm_config))
        boot_vm["ci"] = {"expect": "login:"}
        self.write_extra_profile("single.json", {"vms": {"alpine": boot_vm}})
        args = argparse.Namespace(vm="alpine", timeout=300, dry_run=True)

        with mock.patch.object(vmctl.lifecycle, "cmd_boot_check"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_check_vm(args)

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue()
        self.assertIn("__VMCTL_CHECK_VM_RESULT__", output)
        self.assertIn('"status": "passed"', output)

    def test_run_local_test_vm_subprocess_writes_stdout_and_stderr_logs(self):
        args = argparse.Namespace(timeout=300, dry_run=False)
        completed = subprocess.CompletedProcess(
            args=["vmctl", "_check-vm", "alpha"],
            returncode=0,
            stdout="==> Test VM: alpha\n__VMCTL_CHECK_VM_RESULT__{\"vm\": \"alpha\", \"status\": \"passed\", \"detail\": \"serial boot expectation\"}\n",
            stderr="warning on stderr\n",
        )

        with mock.patch.object(vmctl.lifecycle.subprocess, "run", return_value=completed):
            status, detail, output = vmctl.lifecycle.run_local_test_vm_subprocess("alpha", args)

        self.assertEqual(status, "passed")
        self.assertEqual(detail, "serial boot expectation")
        self.assertIn("Test VM: alpha", output)
        stdout_log = self.root / "artifacts/alpha/logs/check-vms.stdout.log"
        stderr_log = self.root / "artifacts/alpha/logs/check-vms.stderr.log"
        self.assertEqual(stdout_log.read_text(encoding="utf-8"), completed.stdout)
        self.assertEqual(stderr_log.read_text(encoding="utf-8"), completed.stderr)

    def test_cmd_test_local_parallel_runs_workers_and_summarizes_results(self):
        self.write_extra_profile(
            "parallel.json",
            {
                "vms": {
                    "alpha": json.loads(json.dumps(self.vm_config)),
                    "beta": json.loads(json.dumps(self.vm_config)),
                }
            },
        )
        args = argparse.Namespace(vms=["alpha", "beta"], timeout=300, parallel=2, dry_run=True, clean_first=False, no_clean_first=False)

        completed = [
            subprocess.CompletedProcess(
                args=["vmctl", "_check-vm", "alpha"],
                returncode=0,
                stdout="==> Test VM: alpha\n__VMCTL_CHECK_VM_RESULT__{\"vm\": \"alpha\", \"status\": \"passed\", \"detail\": \"serial boot expectation\"}\n",
            ),
            subprocess.CompletedProcess(
                args=["vmctl", "_check-vm", "beta"],
                returncode=0,
                stdout="==> Test VM: beta\n__VMCTL_CHECK_VM_RESULT__{\"vm\": \"beta\", \"status\": \"skipped\", \"detail\": \"missing ci.expect for boot-check\"}\n",
            ),
        ]

        with mock.patch.object(vmctl.host_setup, "prompt_yes_no_default_yes", return_value=False), \
             mock.patch.object(vmctl.lifecycle.subprocess, "run", side_effect=completed) as run_cmd, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_test_local(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_cmd.call_count, 2)
        output = stdout.getvalue()
        self.assertIn("parallel", output)
        self.assertIn("passed", output)
        self.assertIn("skipped", output)
        self.assertIn("alpha", output)
        self.assertIn("beta", output)
        self.assertIn("tail -f artifacts/alpha/logs/check-vms.stdout.log", output)
        self.assertIn("tail -f artifacts/beta/logs/check-vms.stdout.log", output)

    def test_cmd_test_local_returns_nonzero_on_failure(self):
        failing_vm = json.loads(json.dumps(self.vm_config))
        failing_vm["ci"] = {"expect": "login:"}
        self.write_extra_profile("failure.json", {"vms": {"failing": failing_vm}})
        args = argparse.Namespace(vms=["failing"], timeout=300, dry_run=True, clean_first=False, no_clean_first=False)

        with mock.patch.object(vmctl.host_setup, "prompt_yes_no_default_yes", return_value=False), \
             mock.patch.object(vmctl.lifecycle, "cmd_boot_check", side_effect=self.vmctl.VMError("boom")):
            exit_code = self.vmctl.cmd_test_local(args)

        self.assertEqual(exit_code, 1)

    def test_cmd_test_local_stops_bootstrap_vm_even_on_failure(self):
        ubuntu_vm = json.loads(json.dumps(self.vm_config))
        ubuntu_vm["autoinstall"] = {"username": "vmuser", "password_hash": "hash"}
        ubuntu_vm["cloud_init"] = {"user": "vmuser", "ssh_host_port": 2222}
        self.write_extra_profile("bootstrap-failure.json", {"vms": {"ubuntu": ubuntu_vm}})
        args = argparse.Namespace(vms=["ubuntu"], timeout=300, dry_run=True, clean_first=False, no_clean_first=False)

        with mock.patch.object(vmctl.host_setup, "prompt_yes_no_default_yes", return_value=False), \
             mock.patch.object(vmctl.lifecycle, "cmd_bootstrap_unattended", side_effect=self.vmctl.VMError("boom")), \
             mock.patch.object(vmctl.lifecycle, "cmd_stop") as cmd_stop:
            exit_code = self.vmctl.cmd_test_local(args)

        self.assertEqual(exit_code, 1)
        cmd_stop.assert_called_once()
        self.assertEqual(cmd_stop.call_args.args[0].vm, "ubuntu")

    def test_cmd_test_local_clean_first_skips_prompt(self):
        ubuntu_vm = json.loads(json.dumps(self.vm_config))
        ubuntu_vm["autoinstall"] = {"username": "vmuser", "password_hash": "hash"}
        ubuntu_vm["cloud_init"] = {"user": "vmuser", "ssh_host_port": 2222}
        self.write_extra_profile("clean-first.json", {"vms": {"ubuntu": ubuntu_vm}})
        args = argparse.Namespace(vms=["ubuntu"], timeout=300, parallel=1, dry_run=True, clean_first=True, no_clean_first=False)

        with mock.patch.object(vmctl.host_setup, "prompt_yes_no_default_yes") as prompt, \
             mock.patch.object(vmctl.lifecycle, "cmd_bootstrap_unattended") as bootstrap_unattended, \
             mock.patch.object(vmctl.lifecycle, "cmd_stop") as cmd_stop, \
             mock.patch.object(vmctl.lifecycle, "clean_vm") as clean_vm:
            exit_code = self.vmctl.cmd_test_local(args)

        self.assertEqual(exit_code, 0)
        prompt.assert_not_called()
        self.assertEqual(cmd_stop.call_count, 2)
        clean_vm.assert_called_once()
        bootstrap_unattended.assert_called_once()

    def test_cmd_start_cloud_init_attaches_seed_drive(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, cloud_init=True, headless=False, background=False, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.cloud_init, "create_cloud_init_seed", return_value=self.root / "artifacts/testvm/cloud-init/seed.iso") as create_seed, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_start(args)

        self.assertEqual(exit_code, 0)
        create_seed.assert_called_once_with(self.vm_name, self.vm_config, dry_run=True)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-drive", qemu_cmd)
        self.assertIn(
            f"file={self.root / 'artifacts/testvm/cloud-init/seed.iso'},format=raw,if=virtio,readonly=on",
            qemu_cmd,
        )

    def test_cmd_install_unattended_builds_autoinstall_qemu_command(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video="std", dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file") as download_file, \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.cloud_init, "create_autoinstall_seed", return_value=self.root / "artifacts/testvm/autoinstall/seed.iso") as create_seed, \
             mock.patch.object(vmctl.iso, "extract_installer_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")) as extract_boot, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install_unattended(args)

        self.assertEqual(exit_code, 0)
        download_file.assert_called_once_with(self.vm_config["iso_url"], iso_path, dry_run=True, vm=self.vm_config)
        create_seed.assert_called_once_with(self.vm_name, self.vm_config, dry_run=True)
        extract_boot.assert_called_once_with(self.vm_config, iso_path, dry_run=True)
        self.assertEqual(run_cmd.call_args_list[0].args[0][:3], ["qemu-img", "create", "-f"])
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-cdrom", qemu_cmd)
        self.assertIn("-kernel", qemu_cmd)
        self.assertIn(str(self.root / "artifacts/testvm/installer/vmlinuz"), qemu_cmd)
        self.assertIn("-initrd", qemu_cmd)
        self.assertIn(str(self.root / "artifacts/testvm/installer/initrd"), qemu_cmd)
        self.assertIn("-append", qemu_cmd)
        append_value = qemu_cmd[qemu_cmd.index("-append") + 1]
        self.assertIn("autoinstall", append_value)
        self.assertIn("ds=nocloud", append_value)
        self.assertIn("-no-reboot", qemu_cmd)
        self.assertIn(
            f"file={self.root / 'artifacts/testvm/autoinstall/seed.iso'},format=raw,if=virtio,readonly=on",
            qemu_cmd,
        )

    def test_cmd_install_unattended_defaults_to_std_video_for_non_ubuntu_installer(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["video"]["default"] = "virtio-gl"
        self.vm_config["video"]["variants"]["virtio-gl"] = ["-device", "virtio-vga-gl", "-display", "gtk,gl=on"]
        self.vm_config["video"]["variants"]["safe"] = ["-vga", "std", "-display", "gtk", "-serial", "mon:stdio"]
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file"), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.cloud_init, "create_autoinstall_seed", return_value=self.root / "artifacts/testvm/autoinstall/seed.iso"), \
             mock.patch.object(vmctl.iso, "extract_installer_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install_unattended(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)
        self.assertNotIn("-serial", qemu_cmd)
        self.assertNotIn("virtio-vga-gl", qemu_cmd)

    def test_cmd_install_unattended_honours_installer_order_when_set(self):
        iso_path = self.root / self.vm_config["iso"]
        self.vm_config["video"]["default"] = "virtio-gl"
        self.vm_config["video"]["installer_order"] = ["std", "safe"]
        self.vm_config["video"]["variants"]["virtio-gl"] = ["-device", "virtio-vga-gl", "-display", "gtk,gl=on"]
        self.vm_config["video"]["variants"]["safe"] = ["-vga", "std", "-display", "gtk", "-serial", "mon:stdio"]
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file"), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.cloud_init, "create_autoinstall_seed", return_value=self.root / "artifacts/testvm/autoinstall/seed.iso"), \
             mock.patch.object(vmctl.iso, "extract_installer_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install_unattended(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-vga", qemu_cmd)
        self.assertIn("std", qemu_cmd)
        self.assertNotIn("-serial", qemu_cmd)
        self.assertNotIn("virtio-vga-gl", qemu_cmd)

    def test_cmd_install_unattended_headless_uses_no_display(self):
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video="std", headless=True, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file"), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.cloud_init, "create_autoinstall_seed", return_value=self.root / "artifacts/testvm/autoinstall/seed.iso"), \
             mock.patch.object(vmctl.iso, "extract_installer_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_install_unattended(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertIn("-monitor", qemu_cmd)
        self.assertNotIn("-vga", qemu_cmd)
        self.assertNotIn("gtk", qemu_cmd)
        append_value = qemu_cmd[qemu_cmd.index("-append") + 1]
        self.assertIn("autoinstall", append_value)
        self.assertIn("ds=nocloud", append_value)
        self.assertIn("console=ttyS0,115200n8", append_value)
        self.assertIn(
            f"file={self.root / 'artifacts/testvm/autoinstall/seed.iso'},format=raw,if=virtio,readonly=on",
            qemu_cmd,
        )

    def test_cmd_install_unattended_uses_ci_accel_when_present(self):
        self.create_disk()
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.vm_config["ci"] = {"accel": "tcg"}
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video="std", headless=True, dry_run=True)

        with mock.patch.object(vmctl.iso, "download_file"), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.cloud_init, "create_autoinstall_seed", return_value=self.root / "artifacts/testvm/autoinstall/seed.iso"), \
             mock.patch.object(vmctl.iso, "extract_installer_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")), \
             mock.patch.object(vmctl.qemu, "common_args", return_value=["qemu-system-x86_64"]) as common_args, \
             mock.patch.object(vmctl.runtime, "run"):
            exit_code = self.vmctl.cmd_install_unattended(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(common_args.call_args.kwargs["accel"], "tcg")

    def test_cmd_bootstrap_unattended_runs_install_background_start_and_post_install(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "post_install_run": ["echo ready"],
        }
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video="std", timeout=45, dry_run=True)

        with mock.patch.object(vmctl.lifecycle, "cmd_install_unattended") as install_unattended, \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None) as run_background, \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_bootstrap_unattended(args)

        self.assertEqual(exit_code, 0)
        install_args = install_unattended.call_args.args[0]
        self.assertEqual(install_args.vm, self.vm_name)
        self.assertEqual(install_args.video, "std")
        self.assertEqual(install_args.headless, True)
        self.assertEqual(install_args.dry_run, True)
        qemu_cmd = run_background.call_args.args[0]
        log_path = run_background.call_args.args[1]
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertNotIn("-vga", qemu_cmd)
        self.assertIn("-chardev", qemu_cmd)
        self.assertIn("stdio,id=char0,signal=off", qemu_cmd)
        self.assertIn("-serial", qemu_cmd)
        self.assertIn("chardev:char0", qemu_cmd)
        self.assertEqual(log_path, self.root / "artifacts/testvm/logs/bootstrap-start.log")
        wait_for_ssh.assert_called_once_with(self.vm_config, 45, dry_run=True)
        self.assertEqual(
            run_cmd.call_args.args[0][-1],
            "sh -lc 'echo ready'",
        )

    def test_cmd_bootstrap_unattended_always_headless_installer(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, headless=False, timeout=45, dry_run=True)

        with mock.patch.object(vmctl.lifecycle, "cmd_install_unattended") as install_unattended, \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh"), \
             mock.patch.object(vmctl.runtime, "run"):
            exit_code = self.vmctl.cmd_bootstrap_unattended(args)

        self.assertEqual(exit_code, 0)
        install_args = install_unattended.call_args.args[0]
        self.assertEqual(install_args.headless, True)

    def test_cmd_bootstrap_unattended_uses_ci_accel_for_installed_boot(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.vm_config["ci"] = {"accel": "tcg"}
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, headless=False, timeout=45, dry_run=True)

        with mock.patch.object(vmctl.lifecycle, "cmd_install_unattended") as install_unattended, \
             mock.patch.object(vmctl.qemu, "common_args", return_value=["qemu-system-x86_64"]) as common_args, \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh"), \
             mock.patch.object(vmctl.runtime, "run"):
            exit_code = self.vmctl.cmd_bootstrap_unattended(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(install_unattended.call_args.args[0].headless, True)
        self.assertEqual(common_args.call_args.kwargs["accel"], "tcg")

    def test_cmd_bootstrap_unattended_requires_autoinstall(self):
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2222}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_bootstrap_unattended(argparse.Namespace(vm=self.vm_name, video=None, timeout=30, dry_run=True))

    def test_cmd_bootstrap_unattended_requires_ssh_access(self):
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_bootstrap_unattended(argparse.Namespace(vm=self.vm_name, video=None, timeout=30, dry_run=True))

    def test_cmd_bootstrap_archinstall_resets_efi_vars_before_boot(self):
        self.create_disk()
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.vm_config["archinstall_config"] = {
            "hostname": "arch-test",
            "username": "tester",
            "password": "s3cret",
        }
        self.write_config_dir()
        vars_path = self.root / self.vm_config["firmware"]["vars_path"]
        vars_path.parent.mkdir(parents=True, exist_ok=True)
        vars_path.write_text("vars", encoding="utf-8")
        args = argparse.Namespace(vm=self.vm_name, timeout=45, dry_run=False)

        with mock.patch.object(vmctl.iso, "ensure_iso", return_value=self.root / self.vm_config["iso"]), \
             mock.patch.object(vmctl.lifecycle, "ensure_vm_disk"), \
             mock.patch.object(vmctl.archinstall, "create_bootstrap_iso", return_value=self.root / "artifacts/testvm/archinstall/bootstrap.iso"), \
             mock.patch.object(vmctl.iso, "extract_arch_installer_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")), \
             mock.patch.object(vmctl.archinstall, "arch_iso_label", return_value="ARCH_202604"), \
             mock.patch.object(vmctl.qemu, "common_args", side_effect=[["qemu-system-x86_64"], ["qemu-system-x86_64"]]), \
             mock.patch.object(vmctl.qemu, "run_and_expect"), \
             mock.patch.object(vmctl.lifecycle, "prepare_background_vm_slot", return_value=(self.root / "artifacts/testvm/runtime/bootstrap-start.pid", self.root / "artifacts/testvm/logs/bootstrap-start.log")), \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None), \
             mock.patch.object(vmctl.lifecycle, "run_post_install"):
            exit_code = self.vmctl.cmd_bootstrap_archinstall(args)

        self.assertEqual(exit_code, 0)
        self.assertFalse(vars_path.exists())

    def test_cmd_bootstrap_preseed_dry_run_allows_missing_disk_for_post_install_boot(self):
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.vm_config["preseed_config"] = {
            "hostname": "debian-test",
            "username": "tester",
            "password": "s3cret",
        }
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2228}
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=45, dry_run=True)

        with mock.patch.object(vmctl.iso, "ensure_iso", return_value=self.root / self.vm_config["iso"]), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.preseed, "extract_preseed_boot_artifacts", return_value=(self.root / "artifacts/testvm/preseed/vmlinuz", self.root / "artifacts/testvm/preseed/initrd")), \
             mock.patch.object(vmctl.qemu, "common_args", side_effect=[["qemu-system-x86_64"], ["qemu-system-x86_64"]]), \
             mock.patch.object(vmctl.lifecycle, "run_post_install") as run_post_install, \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None):
            exit_code = self.vmctl.cmd_bootstrap_preseed(args)

        self.assertEqual(exit_code, 0)
        run_post_install.assert_called_once_with(self.vm_name, self.vm_config, 45, dry_run=True)

    def test_cmd_bootstrap_kickstart_runs_install_wait_and_post_install(self):
        self.create_disk()
        self.vm_config["kickstart_config"] = {
            "hostname": "alma-test",
            "username": "tester",
            "password": "s3cret",
        }
        self.vm_config["ssh_provision"] = {
            "user": "tester",
            "ssh_host_port": 2229,
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=45, dry_run=False)

        with mock.patch.object(vmctl.iso, "ensure_iso", return_value=self.root / self.vm_config["iso"]), \
             mock.patch.object(vmctl.lifecycle, "ensure_vm_disk"), \
             mock.patch.object(vmctl.lifecycle, "reset_vm_nvram"), \
             mock.patch.object(vmctl.kickstart, "create_kickstart_iso", return_value=self.root / "artifacts/testvm/kickstart/seed.iso"), \
             mock.patch.object(vmctl.kickstart, "extract_kickstart_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")), \
             mock.patch.object(vmctl.qemu, "common_args", side_effect=[["qemu-system-x86_64"], ["qemu-system-x86_64"]]), \
             mock.patch.object(vmctl.qemu, "run_and_expect") as run_and_expect, \
             mock.patch.object(vmctl.lifecycle, "prepare_background_vm_slot", return_value=(self.root / "artifacts/testvm/runtime/bootstrap-start.pid", self.root / "artifacts/testvm/logs/bootstrap-start.log")), \
             mock.patch.object(vmctl.runtime, "run_background", return_value=4321) as run_background, \
             mock.patch.object(vmctl.lifecycle, "run_post_install") as run_post_install:
            exit_code = self.vmctl.cmd_bootstrap_kickstart(args)

        self.assertEqual(exit_code, 0)
        run_and_expect.assert_called_once()
        install_qemu_cmd = run_and_expect.call_args.args[0]
        self.assertEqual(install_qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn("-cdrom", install_qemu_cmd)
        self.assertIn(str(self.root / self.vm_config["iso"]), install_qemu_cmd)
        self.assertIn("-append", install_qemu_cmd)
        append_value = install_qemu_cmd[install_qemu_cmd.index("-append") + 1]
        self.assertIn("inst.ks=hd:LABEL=KS_CFG:/ks.cfg", append_value)
        self.assertIn("console=ttyS0,115200", append_value)
        self.assertEqual(run_and_expect.call_args.kwargs["expected_text"], vmctl.kickstart.BOOTSTRAP_COMPLETE_TOKEN)
        self.assertEqual(run_and_expect.call_args.kwargs["timeout_sec"], 45)
        self.assertEqual(
            run_and_expect.call_args.kwargs["log_path"],
            self.root / "artifacts/testvm/logs/bootstrap-serial.log",
        )

        run_qemu_cmd = run_background.call_args.args[0]
        self.assertEqual(run_qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn("-serial", run_qemu_cmd)
        self.assertIn(
            f"file:{self.root / 'artifacts/testvm/logs/post-install-serial.log'}",
            run_qemu_cmd,
        )
        pid_path = self.root / "artifacts/testvm/runtime/bootstrap-start.pid"
        self.assertEqual(pid_path.read_text(encoding="utf-8"), "4321\n")
        run_post_install.assert_called_once_with(self.vm_name, self.vm_config, 45, dry_run=False)

    def test_cmd_bootstrap_kickstart_dry_run_allows_missing_disk_for_post_install_boot(self):
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.vm_config["kickstart_config"] = {
            "hostname": "alma-test",
            "username": "tester",
            "password": "s3cret",
        }
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2229}
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=45, dry_run=True)

        with mock.patch.object(vmctl.iso, "ensure_iso", return_value=self.root / self.vm_config["iso"]), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.kickstart, "create_kickstart_iso", return_value=self.root / "artifacts/testvm/kickstart/seed.iso"), \
             mock.patch.object(vmctl.kickstart, "extract_kickstart_boot_artifacts", return_value=(self.root / "artifacts/testvm/installer/vmlinuz", self.root / "artifacts/testvm/installer/initrd")), \
             mock.patch.object(vmctl.qemu, "common_args", side_effect=[["qemu-system-x86_64"], ["qemu-system-x86_64"]]), \
             mock.patch.object(vmctl.lifecycle, "run_post_install") as run_post_install, \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None):
            exit_code = self.vmctl.cmd_bootstrap_kickstart(args)

        self.assertEqual(exit_code, 0)
        run_post_install.assert_called_once_with(self.vm_name, self.vm_config, 45, dry_run=True)


if __name__ == "__main__":
    unittest.main()
