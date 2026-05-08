import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.runtime  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class CloudInitTests(BaseVmctlTestCase):
    def test_create_cloud_init_seed_writes_artifacts_and_runs_cloud_localds(self):
        pubkey = self.root / ".ssh" / "id_ed25519.pub"
        pubkey.parent.mkdir(parents=True, exist_ok=True)
        pubkey.write_text("ssh-ed25519 AAAA from-file\n", encoding="utf-8")
        self.vm_config["cloud_init"] = {
            "hostname": "testvm",
            "user": "tester",
            "ssh_authorized_keys": ["ssh-ed25519 AAAA test"],
            "ssh_authorized_keys_file": str(pubkey),
            "packages": ["niri"],
            "runcmd": ["echo ready"],
        }

        with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/cloud-localds" if name == "cloud-localds" else None), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            seed_path = self.vmctl.create_cloud_init_seed(self.vm_name, self.vm_config)

        self.assertEqual(seed_path, self.root / "artifacts/testvm/cloud-init/seed.iso")
        user_data = (self.root / "artifacts/testvm/cloud-init/user-data").read_text(encoding="utf-8")
        self.assertIn("#cloud-config", user_data)
        self.assertIn("ssh-ed25519 AAAA test", user_data)
        self.assertIn("ssh-ed25519 AAAA from-file", user_data)
        self.assertIn('"local-hostname": "testvm"', (self.root / "artifacts/testvm/cloud-init/meta-data").read_text(encoding="utf-8"))
        self.assertEqual(
            run_cmd.call_args.args[0],
            [
                "cloud-localds",
                str(self.root / "artifacts/testvm/cloud-init/seed.iso"),
                str(self.root / "artifacts/testvm/cloud-init/user-data"),
                str(self.root / "artifacts/testvm/cloud-init/meta-data"),
            ],
        )

    def test_collect_ssh_authorized_keys_requires_existing_file(self):
        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.collect_ssh_authorized_keys({"ssh_authorized_keys_file": str(self.root / "missing.pub")})

    def test_collect_ssh_authorized_keys_allows_missing_file_in_dry_run_mode(self):
        keys = self.vmctl.collect_ssh_authorized_keys(
            {"ssh_authorized_keys_file": str(self.root / "missing.pub")},
            allow_missing_file=True,
        )

        self.assertEqual(keys, [])

    def test_render_autoinstall_user_data_embeds_identity_ssh_and_first_boot_cloud_init(self):
        pubkey = self.root / ".ssh" / "id_ed25519.pub"
        pubkey.parent.mkdir(parents=True, exist_ok=True)
        pubkey.write_text("ssh-ed25519 AAAA from-file\n", encoding="utf-8")
        self.vm_config["cloud_init"] = {
            "hostname": "testvm",
            "user": "tester",
            "ssh_authorized_keys_file": str(pubkey),
            "packages": ["niri"],
            "runcmd": ["echo ready"],
        }
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
            "timezone": "Europe/Rome",
        }

        rendered = self.vmctl.render_autoinstall_user_data(self.vm_name, self.vm_config)

        self.assertIn('"username": "tester"', rendered)
        self.assertIn('"password": "$6$hash"', rendered)
        self.assertIn('"authorized-keys": [', rendered)
        self.assertIn('"ssh-ed25519 AAAA from-file"', rendered)
        self.assertIn('"user-data": {', rendered)
        self.assertIn('"packages": [', rendered)
        self.assertIn('"runcmd": [', rendered)

    def test_render_autoinstall_user_data_rejects_invalid_updates_value(self):
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
            "updates": "none",
        }

        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.render_autoinstall_user_data(self.vm_name, self.vm_config)

    def test_collect_ssh_authorized_keys_reads_public_key_from_ssh_key(self):
        private = self.root / ".ssh" / "id_ed25519"
        public = private.parent / "id_ed25519.pub"
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text("private", encoding="utf-8")
        public.write_text("ssh-ed25519 AAAA from-ssh-key\n", encoding="utf-8")

        keys = self.vmctl.collect_ssh_authorized_keys(
            {"ssh_key": str(private)},
            ssh_public_key=public,
        )

        self.assertEqual(keys, ["ssh-ed25519 AAAA from-ssh-key"])

    def test_create_unattended_seed_writes_preseed_and_runs_genisoimage(self):
        pubkey = self.root / ".ssh" / "id_ed25519.pub"
        pubkey.parent.mkdir(parents=True, exist_ok=True)
        pubkey.write_text("ssh-ed25519 AAAA from-file\n", encoding="utf-8")
        self.vm_config["ssh_provision"] = {
            "hostname": "testvm",
            "user": "tester",
            "ssh_host_port": 2222,
            "ssh_key": str(self.root / ".ssh" / "id_ed25519"),
        }
        self.vm_config["unattended"] = {
            "type": "debian-preseed",
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }

        with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/genisoimage" if name == "genisoimage" else None), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            seed_path = self.vmctl.create_unattended_seed(self.vm_name, self.vm_config)

        self.assertEqual(seed_path, self.root / "artifacts/testvm/unattended/seed.iso")
        rendered = (self.root / "artifacts/testvm/unattended/preseed.cfg").read_text(encoding="utf-8")
        self.assertIn("auto-install/cloak_initrd_preseed boolean true", rendered)
        self.assertIn("preseed/late_command", rendered)
        self.assertIn("ssh-ed25519 AAAA from-file", rendered)
        self.assertEqual(
            run_cmd.call_args.args[0],
            [
                "genisoimage",
                "-output",
                str(self.root / "artifacts/testvm/unattended/seed.iso"),
                "-volid",
                "cidata",
                "-joliet",
                "-rock",
                str(self.root / "artifacts/testvm/unattended/preseed.cfg"),
            ],
        )

    def test_render_kickstart_config_includes_user_packages_and_ssh_key(self):
        pubkey = self.root / ".ssh" / "id_ed25519.pub"
        pubkey.parent.mkdir(parents=True, exist_ok=True)
        pubkey.write_text("ssh-ed25519 AAAA from-file\n", encoding="utf-8")
        self.vm_config["ssh_provision"] = {
            "hostname": "testvm",
            "user": "tester",
            "ssh_host_port": 2222,
            "ssh_key": str(self.root / ".ssh" / "id_ed25519"),
        }
        self.vm_config["unattended"] = {
            "type": "fedora-kickstart",
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
            "packages": ["qemu-guest-agent"],
        }

        rendered = self.vmctl.render_kickstart_config(self.vm_name, self.vm_config)

        self.assertIn("ignoredisk --only-use=vda", rendered)
        self.assertIn("autopart --type=lvm", rendered)
        self.assertIn("network --bootproto=dhcp --activate --hostname=testvm", rendered)
        self.assertIn("user --name=tester", rendered)
        self.assertIn("openssh-server", rendered)
        self.assertIn("qemu-guest-agent", rendered)
        self.assertIn("ssh-ed25519 AAAA from-file", rendered)

    def test_unattended_kernel_append_uses_http_url_for_debian_preseed(self):
        self.vm_config["unattended"] = {
            "type": "debian-preseed",
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
            "locale": "it_IT.UTF-8",
            "keyboard_layout": "it",
        }

        rendered = self.vmctl.unattended_kernel_append(
            self.vm_name,
            self.vm_config,
            http_url="http://10.0.2.2:12345/preseed.cfg",
        )

        self.assertIn("auto", rendered.split())
        self.assertNotIn("auto=true", rendered.split())
        self.assertNotIn("auto-install/enable=true", rendered.split())
        self.assertIn("url=http://10.0.2.2:12345/preseed.cfg", rendered)
        self.assertIn("preseed/url=http://10.0.2.2:12345/preseed.cfg", rendered)
        self.assertIn("language=it", rendered)
        self.assertIn("country=IT", rendered)
        self.assertIn("locale=it_IT.UTF-8", rendered)
        self.assertIn("debian-installer/locale=it_IT.UTF-8", rendered)
        self.assertIn("localechooser/supported-locales=it_IT.UTF-8", rendered)
        self.assertIn("keyboard-configuration/xkb-keymap=it", rendered)
        self.assertIn("keymap=it", rendered)

    def test_unattended_kernel_append_without_http_url_does_not_use_cdrom_path(self):
        self.vm_config["unattended"] = {
            "type": "debian-preseed",
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
            "locale": "it_IT.UTF-8",
            "keyboard_layout": "it",
        }

        rendered = self.vmctl.unattended_kernel_append(self.vm_name, self.vm_config)

        self.assertIn("auto", rendered.split())
        self.assertNotIn("auto=true", rendered.split())
        self.assertNotIn("auto-install/enable=true", rendered.split())
        self.assertIn("file=/preseed.cfg", rendered)
        self.assertIn("preseed/file=/preseed.cfg", rendered)
        self.assertNotIn("preseed/file=/cdrom/preseed.cfg", rendered)


if __name__ == "__main__":
    unittest.main()
