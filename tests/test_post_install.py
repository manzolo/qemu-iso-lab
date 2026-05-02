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


class PostInstallTests(BaseVmctlTestCase):
    def test_ssh_base_cmd_allows_missing_private_key_in_dry_run_mode(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "ssh_key": str(self.root / "missing-key"),
        }

        cmd = self.vmctl.ssh_base_cmd(self.vm_config, dry_run=True)

        self.assertEqual(cmd[0], "ssh")
        self.assertNotIn("-i", cmd)

    def test_ssh_base_cmd_requires_existing_private_key_when_not_dry_run(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "ssh_key": str(self.root / "missing-key"),
        }

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.ssh_base_cmd(self.vm_config, dry_run=False)

    def test_ssh_shell_cmd_omits_batch_mode(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }

        cmd = self.vmctl.ssh_shell_cmd(self.vm_config, dry_run=True)

        self.assertEqual(cmd[0], "ssh")
        self.assertEqual(cmd[1:3], ["-F", "/dev/null"])
        self.assertNotIn("BatchMode=yes", cmd)
        self.assertEqual(cmd[-1], "tester@127.0.0.1")

    def test_wait_for_ssh_retries_until_probe_succeeds(self):
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
        }
        self.write_config_dir()

        results = [
            self.vmctl.subprocess.CompletedProcess(args=["ssh"], returncode=255),
            self.vmctl.subprocess.CompletedProcess(args=["ssh"], returncode=0),
        ]
        with mock.patch.object(subprocess, "run", side_effect=results) as run_cmd, \
             mock.patch.object(time, "sleep") as sleep_mock:
            self.vmctl.wait_for_ssh(self.vm_config, timeout_sec=10, dry_run=False)

        self.assertEqual(run_cmd.call_count, 2)
        self.assertEqual(run_cmd.call_args_list[0].args[0][-1], "true")
        sleep_mock.assert_called_once_with(2)

    def test_cmd_post_install_waits_copies_and_runs_remote_commands(self):
        source_dir = self.root / "host-niri"
        source_dir.mkdir()
        (source_dir / "config.kdl").write_text("layout {}", encoding="utf-8")
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "copy_from_host": [{"source": str(source_dir) + "/", "dest": "/home/tester/.config/niri"}],
            "post_install_run": ["sudo apt update", "sudo apt install -y niri"],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(vmctl.ssh, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=True)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=True)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(
            executed[0],
            [
                "ssh",
                "-F",
                "/dev/null",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "BatchMode=yes",
                "-p",
                "2222",
                "tester@127.0.0.1",
                "sh -lc 'mkdir -p /home/tester/.config/niri'",
            ],
        )
        self.assertEqual(
            executed[1],
            mock.ANY,
        )
        self.assertEqual(executed[1][0], "scp")
        self.assertEqual(executed[1][-1], "tester@127.0.0.1:/home/tester/.config/niri")
        self.assertTrue(executed[1][-2].endswith("/host-niri/."))
        self.assertEqual(
            executed[2][-1],
            "sh -lc 'sudo apt update'",
        )
        self.assertEqual(
            executed[3][-1],
            "sh -lc 'sudo apt install -y niri'",
        )

    def test_cmd_post_install_supports_ssh_provision(self):
        source_dir = self.root / "host-niri"
        source_dir.mkdir()
        (source_dir / "config.kdl").write_text("layout {}", encoding="utf-8")
        self.vm_config["ssh_provision"] = {
            "user": "tester",
            "ssh_host_port": 2223,
            "copy_from_host": [{"source": str(source_dir) + "/", "dest": "/home/tester/.config/niri"}],
            "post_install_run": ["sudo pacman -Sy --noconfirm --needed foot niri || true"],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(vmctl.ssh, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=True)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=True)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0][-1], "sh -lc 'mkdir -p /home/tester/.config/niri'")
        self.assertEqual(executed[1][-1], "tester@127.0.0.1:/home/tester/.config/niri")
        self.assertEqual(
            executed[2][-1],
            "sh -lc 'sudo pacman -Sy --noconfirm --needed foot niri || true'",
        )

    def test_cmd_post_install_supports_sudo_copy_for_system_connections(self):
        source_file = self.root / "vpn.nmconnection"
        source_file.write_text("[connection]\nid=test\n", encoding="utf-8")
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "copy_from_host": [
                {
                    "source": str(source_file),
                    "source_sudo": True,
                    "dest": "/etc/NetworkManager/system-connections/test.nmconnection",
                    "dest_sudo": True,
                    "dest_mode": "600",
                }
            ],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(vmctl.ssh, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=True)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=True)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0], ["sudo", "cp", "--archive", str(source_file), mock.ANY])
        self.assertEqual(
            executed[1][-1],
            "sudo sh -lc 'mkdir -p /etc/NetworkManager/system-connections'",
        )
        self.assertTrue(executed[2][-1].startswith("tester@127.0.0.1:/tmp/test.nmconnection"))
        self.assertEqual(
            executed[3][-1],
            "sudo sh -lc 'install -D -m 600 /tmp/test.nmconnection /etc/NetworkManager/system-connections/test.nmconnection && rm -f /tmp/test.nmconnection'",
        )

    def test_cmd_post_install_recursive_copy_stages_directory_before_scp(self):
        source_dir = self.root / "host-geany"
        source_dir.mkdir()
        (source_dir / "geany.conf").write_text("config", encoding="utf-8")
        (source_dir / "runtime-link").symlink_to(source_dir / "geany.conf")
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "copy_from_host": [{"source": str(source_dir) + "/", "dest": "/home/tester/.config/geany"}],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(vmctl.ssh, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=True)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=True)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0][-1], "sh -lc 'mkdir -p /home/tester/.config/geany'")
        self.assertEqual(executed[1][0], "scp")
        self.assertTrue(executed[1][-2].endswith("/."))
        self.assertEqual(executed[1][-1], "tester@127.0.0.1:/home/tester/.config/geany")

    def test_cmd_post_install_recursive_copy_skips_dangling_symlink(self):
        source_dir = self.root / "host-geany-dangling"
        source_dir.mkdir()
        (source_dir / "geany.conf").write_text("config", encoding="utf-8")
        (source_dir / "geany_socket_wayland-0").symlink_to(source_dir / "missing-socket")
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "copy_from_host": [{"source": str(source_dir) + "/", "dest": "/home/tester/.config/geany"}],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=True)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(vmctl.ssh, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=True)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=True)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(executed[0][-1], "sh -lc 'mkdir -p /home/tester/.config/geany'")
        self.assertEqual(executed[1][0], "scp")
        self.assertEqual(executed[1][-1], "tester@127.0.0.1:/home/tester/.config/geany")

    def test_cmd_post_install_skips_missing_host_path(self):
        missing_dir = self.root / "missing-config"
        self.vm_config["cloud_init"] = {
            "user": "tester",
            "ssh_host_port": 2222,
            "copy_from_host": [{"source": str(missing_dir) + "/", "dest": "/home/tester/.config/missing"}],
            "post_install_run": ["echo done"],
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=30, dry_run=False)

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh") as wait_for_ssh, \
             mock.patch.object(vmctl.ssh, "wait_for_guest_post_install_ready") as wait_ready, \
             mock.patch.object(vmctl.runtime, "run") as run_cmd, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = self.vmctl.cmd_post_install(args)

        self.assertEqual(exit_code, 0)
        wait_for_ssh.assert_called_once_with(self.vm_config, 30, dry_run=False)
        wait_ready.assert_called_once_with(self.vm_config, dry_run=False)
        executed = [call.args[0] for call in run_cmd.call_args_list]
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0][-1], "sh -lc 'echo done'")
        self.assertIn("Skipping missing host path", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
