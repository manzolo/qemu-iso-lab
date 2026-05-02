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
        self.assertIn("hostfwd:2222", output)
        self.assertIn("pid=4242", output)

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

    def test_cmd_install_defaults_to_safe_video_for_non_ubuntu_installer(self):
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
        self.assertIn("-serial", qemu_cmd)
        self.assertNotIn("virtio-vga-gl", qemu_cmd)

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
            f"file={self.root / 'artifacts/testvm/cloud-init/seed.iso'},format=raw,if=virtio,media=cdrom,readonly=on",
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
        self.assertIn("autoinstall", qemu_cmd)
        self.assertIn("-no-reboot", qemu_cmd)
        self.assertIn(
            f"file={self.root / 'artifacts/testvm/autoinstall/seed.iso'},format=raw,if=virtio,media=cdrom,readonly=on",
            qemu_cmd,
        )

    def test_cmd_install_unattended_defaults_to_safe_video_for_non_ubuntu_installer(self):
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
        self.assertIn("-serial", qemu_cmd)
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
        self.assertEqual(install_args.headless, False)
        self.assertEqual(install_args.dry_run, True)
        qemu_cmd = run_background.call_args.args[0]
        log_path = run_background.call_args.args[1]
        self.assertEqual(qemu_cmd[0], "qemu-system-x86_64")
        self.assertIn("-display", qemu_cmd)
        self.assertIn("none", qemu_cmd)
        self.assertNotIn("-vga", qemu_cmd)
        self.assertEqual(log_path, self.root / "artifacts/testvm/logs/bootstrap-start.log")
        wait_for_ssh.assert_called_once_with(self.vm_config, 45, dry_run=True)
        self.assertEqual(
            run_cmd.call_args.args[0][-1],
            "sh -lc 'echo ready'",
        )

    def test_cmd_bootstrap_unattended_forwards_headless_to_installer(self):
        self.create_disk()
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video=None, headless=True, timeout=45, dry_run=True)

        with mock.patch.object(vmctl.lifecycle, "cmd_install_unattended") as install_unattended, \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh"), \
             mock.patch.object(vmctl.runtime, "run"):
            exit_code = self.vmctl.cmd_bootstrap_unattended(args)

        self.assertEqual(exit_code, 0)
        install_args = install_unattended.call_args.args[0]
        self.assertEqual(install_args.headless, True)

    def test_cmd_bootstrap_unattended_requires_autoinstall(self):
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_bootstrap_unattended(argparse.Namespace(vm=self.vm_name, video=None, timeout=30, dry_run=True))

    def test_cmd_bootstrap_unattended_requires_cloud_init(self):
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_bootstrap_unattended(argparse.Namespace(vm=self.vm_name, video=None, timeout=30, dry_run=True))


if __name__ == "__main__":
    unittest.main()
