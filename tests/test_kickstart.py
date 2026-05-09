import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.kickstart  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.ssh  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class KickstartConfigTests(BaseVmctlTestCase):
    def _kickstart_vm(self) -> None:
        self.vm_config["kickstart_config"] = {
            "hostname": "almalinux-test",
            "username": "tester",
            "password": "s3cret",
            "timezone": "Europe/Rome",
            "keyboard_layout": "it",
            "locale": "it_IT.UTF-8",
            "packages": ["@^minimal-environment", "openssh-server", "sudo", "vim-minimal"],
            "post_commands": [],
            "disk_device": "vda",
            "selinux": "enforcing",
            "firewall": "enabled"
        }

    def test_render_kickstart_hostname_and_locale(self):
        self._kickstart_vm()
        rendered = vmctl.kickstart.render_kickstart(self.vm_name, self.vm_config)
        self.assertIn("network --bootproto=dhcp --hostname=almalinux-test", rendered)
        self.assertIn("timezone Europe/Rome --utc", rendered)
        self.assertIn("lang it_IT.UTF-8", rendered)
        self.assertIn("keyboard it", rendered)

    def test_render_kickstart_includes_packages(self):
        self._kickstart_vm()
        rendered = vmctl.kickstart.render_kickstart(self.vm_name, self.vm_config)
        self.assertIn("%packages\n@^minimal-environment\nopenssh-server\nsudo\nvim-minimal\n%end", rendered)

    def test_render_kickstart_raises_without_username(self):
        self._kickstart_vm()
        del self.vm_config["kickstart_config"]["username"]
        with self.assertRaises(vmctl.kickstart.VMError):
            vmctl.kickstart.render_kickstart(self.vm_name, self.vm_config)

    def test_render_kickstart_raises_without_password(self):
        self._kickstart_vm()
        del self.vm_config["kickstart_config"]["password"]
        with self.assertRaises(vmctl.kickstart.VMError):
            vmctl.kickstart.render_kickstart(self.vm_name, self.vm_config)

    def test_render_kickstart_sudoers_and_selinux(self):
        self._kickstart_vm()
        rendered = vmctl.kickstart.render_kickstart(self.vm_name, self.vm_config)
        self.assertIn("echo '%wheel ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/nopasswd-wheel", rendered)
        self.assertIn("selinux --enforcing", rendered)

    def test_render_kickstart_bootloader_efi_safe(self):
        self._kickstart_vm()
        rendered = vmctl.kickstart.render_kickstart(self.vm_name, self.vm_config)
        # Anaconda autodetects the right bootloader location based on firmware
        # (BIOS -> mbr, EFI -> ESP). Forcing --location=mbr breaks EFI installs.
        self.assertNotIn("--location=mbr", rendered)
        self.assertIn("bootloader", rendered)

    def test_render_kickstart_custom_commands(self):
        self._kickstart_vm()
        self.vm_config["kickstart_config"]["post_commands"] = ["touch /tmp/custom"]
        rendered = vmctl.kickstart.render_kickstart(self.vm_name, self.vm_config)
        self.assertIn("touch /tmp/custom", rendered)

    def test_render_kickstart_generates_ssh_key_for_ssh_provision(self):
        self._kickstart_vm()
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2223}
        key_path = self.root / "artifacts/testvm/ssh/id_ed25519"
        pub_path = self.root / "artifacts/testvm/ssh/id_ed25519.pub"
        pub_path.parent.mkdir(parents=True)
        pub_path.write_text("ssh-ed25519 AAAATEST generated\n", encoding="utf-8")

        with mock.patch.object(vmctl.ssh, "ensure_generated_ssh_keypair", return_value=key_path):
            script = vmctl.kickstart.render_kickstart(self.vm_name, self.vm_config)

        self.assertIn("Installing SSH public key for tester", script)
        self.assertIn("ssh-ed25519 AAAATEST generated", script)
        self.assertIn("restorecon -R /home/tester/.ssh", script)

    def test_render_kickstart_ends_with_complete_token(self):
        self._kickstart_vm()
        script = vmctl.kickstart.render_kickstart(self.vm_name, self.vm_config)
        self.assertIn('echo "==> Kickstart install complete!" > /dev/console 2>&1 || true', script)
        self.assertIn('echo "==> Kickstart install complete!" > /dev/ttyS0 2>&1 || true', script)
        self.assertIn("sync", script)
        self.assertIn("blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true", script)
        self.assertIn(vmctl.kickstart.BOOTSTRAP_COMPLETE_TOKEN, script)

class KickstartBootstrapTests(BaseVmctlTestCase):
    def _kickstart_vm(self) -> None:
        self.vm_config["kickstart_config"] = {
            "hostname": "almalinux-test",
            "username": "tester",
            "password": "s3cret",
        }

    def test_create_kickstart_iso_writes_files_and_calls_builder(self):
        self._kickstart_vm()
        with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/xorriso" if name == "xorriso" else None), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            iso_path = vmctl.kickstart.create_kickstart_iso(self.vm_name, self.vm_config)

        artifact_dir = self.root / "artifacts/testvm/kickstart"
        self.assertTrue((artifact_dir / "ks.cfg").exists())
        self.assertEqual(iso_path, artifact_dir / "seed.iso")

        cmd = run_cmd.call_args.args[0]
        self.assertIn("xorriso", cmd[0])
        self.assertIn("KS_CFG", cmd)

    def test_kickstart_iso_drive_args_uses_virtio_cdrom(self):
        iso_path = Path("/tmp/seed.iso")
        args = vmctl.kickstart.kickstart_iso_drive_args(iso_path)
        self.assertEqual(len(args), 2)
        self.assertEqual(args[0], "-drive")
        self.assertIn("if=virtio", args[1])
        self.assertIn("media=cdrom", args[1])
        self.assertIn(str(iso_path), args[1])

if __name__ == "__main__":
    unittest.main()
