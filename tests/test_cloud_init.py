import shutil
import sys
import unittest
import json
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
        payload = json.loads(rendered.removeprefix("#cloud-config\n"))
        autoinstall = payload["autoinstall"]

        self.assertIn('"username": "tester"', rendered)
        self.assertIn('"password": "$6$hash"', rendered)
        self.assertEqual(autoinstall["source"]["search_drivers"], False)
        self.assertEqual(autoinstall["drivers"]["install"], False)
        self.assertEqual(autoinstall["oem"]["install"], False)
        self.assertEqual(autoinstall["codecs"]["install"], False)
        self.assertIn('"authorized-keys": [', rendered)
        self.assertIn('"ssh-ed25519 AAAA from-file"', rendered)
        self.assertIn('"user-data": {', rendered)
        self.assertIn('"packages": [', rendered)
        self.assertIn('"runcmd": [', rendered)

    def test_render_autoinstall_user_data_allows_overriding_driver_and_oem_flags(self):
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
            "search_drivers": True,
            "install_drivers": True,
            "install_oem": True,
            "install_codecs": True,
        }

        rendered = self.vmctl.render_autoinstall_user_data(self.vm_name, self.vm_config)
        payload = json.loads(rendered.removeprefix("#cloud-config\n"))
        autoinstall = payload["autoinstall"]

        self.assertEqual(autoinstall["source"]["search_drivers"], True)
        self.assertEqual(autoinstall["drivers"]["install"], True)
        self.assertEqual(autoinstall["oem"]["install"], True)
        self.assertEqual(autoinstall["codecs"]["install"], True)

    def test_render_autoinstall_user_data_generates_authorized_key_from_cloud_init_access(self):
        generated_private = self.root / "artifacts/testvm/ssh/id_ed25519"
        generated_public = generated_private.parent / "id_ed25519.pub"
        generated_public.parent.mkdir(parents=True, exist_ok=True)
        generated_private.write_text("private", encoding="utf-8")
        generated_public.write_text("ssh-ed25519 AAAA generated\n", encoding="utf-8")
        self.vm_config["cloud_init"] = {
            "hostname": "testvm",
            "user": "tester",
            "ssh_host_port": 2222,
        }
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "tester",
            "password_hash": "$6$hash",
        }

        with mock.patch.object(self.vmctl.ssh, "ensure_generated_ssh_keypair", return_value=generated_private):
            rendered = self.vmctl.render_autoinstall_user_data(self.vm_name, self.vm_config)

        self.assertIn('"authorized-keys": [', rendered)
        self.assertIn('"ssh-ed25519 AAAA generated"', rendered)

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

if __name__ == "__main__":
    unittest.main()
