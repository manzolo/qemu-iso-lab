import argparse
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.cloud_init  # noqa: E402
import vmctl.lifecycle  # noqa: E402
import vmctl.omarchy  # noqa: E402
import vmctl.runtime  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class OmarchyConfigTests(BaseVmctlTestCase):
    def _omarchy_vm(self) -> None:
        self.vm_config["disk"]["size"] = "64G"
        self.vm_config["omarchy_config"] = {
            "hostname": "omarchy-test",
            "username": "tester",
            "password_hash": "$6$hash",
            "timezone": "Europe/Rome",
            "keyboard_layout": "it",
            "locale": "en_US.UTF-8",
            "disk_device": "/dev/vda",
            "encrypt": False,
        }

    def test_render_user_configuration_matches_official_full_disk_layout(self):
        self._omarchy_vm()
        payload = json.loads(vmctl.omarchy.render_user_configuration(self.vm_name, self.vm_config))

        self.assertEqual(payload["bootloader_config"]["bootloader"], "Limine")
        self.assertEqual(payload["omarchy_install"]["mode"], "full_disk")
        self.assertEqual(payload["hostname"], "omarchy-test")
        self.assertEqual(payload["timezone"], "Europe/Rome")
        self.assertEqual(payload["locale_config"]["kb_layout"], "it")
        self.assertEqual(payload["disk_config"]["device_modifications"][0]["device"], "/dev/vda")
        partitions = payload["disk_config"]["device_modifications"][0]["partitions"]
        self.assertEqual(partitions[0]["mountpoint"], "/boot")
        self.assertEqual(partitions[0]["size"]["value"], 2 * 1024**3)
        self.assertEqual(partitions[1]["fs_type"], "btrfs")
        self.assertEqual(partitions[1]["size"]["value"], 64 * 1024**3 - 2 * 1024**3 - 2 * 1024**2)
        self.assertNotIn("disk_encryption", payload["disk_config"])
        self.assertEqual(payload["custom_commands"], [])

    def test_render_user_credentials_uses_hash_and_username(self):
        self._omarchy_vm()
        payload = json.loads(vmctl.omarchy.render_user_credentials(self.vm_config))

        self.assertEqual(payload["root_enc_password"], "$6$hash")
        self.assertEqual(payload["users"][0]["username"], "tester")
        self.assertTrue(payload["users"][0]["sudo"])

    def test_create_cidata_iso_uses_official_filenames_and_label(self):
        self._omarchy_vm()
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2222}
        expected = self.root / "artifacts/testvm/omarchy/seed.iso"

        with mock.patch.object(vmctl.cloud_init, "_authorized_keys_for_vm", return_value=["ssh-ed25519 AAAA test"]), \
             mock.patch.object(vmctl.cloud_init, "create_iso_with_files", return_value=expected) as create_iso:
            result = vmctl.omarchy.create_cidata_iso(self.vm_name, self.vm_config)

        self.assertEqual(result, expected)
        artifact_dir, files = create_iso.call_args.args
        self.assertEqual(artifact_dir, self.root / "artifacts/testvm/omarchy")
        self.assertEqual(create_iso.call_args.kwargs["volume_id"], "cidata")
        self.assertIn("user_configuration.json", files)
        self.assertIn("user_credentials.json", files)
        self.assertEqual(files["authorized_keys"], "ssh-ed25519 AAAA test\n")
        self.assertEqual(files["user_encrypt_installation.txt"], "false\n")

    def test_rejects_disk_smaller_than_32g(self):
        self._omarchy_vm()
        self.vm_config["disk"]["size"] = "24G"

        with self.assertRaises(vmctl.omarchy.VMError):
            vmctl.omarchy.render_user_configuration(self.vm_name, self.vm_config)


class OmarchyLifecycleTests(BaseVmctlTestCase):
    def test_cmd_install_omarchy_attaches_official_iso_and_cidata(self):
        self.vm_config["disk"]["size"] = "64G"
        self.vm_config["omarchy_config"] = {
            "username": "tester",
            "password_hash": "$6$hash",
        }
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, video="std", headless=False, spice_port=None, dry_run=True)
        seed = self.root / "artifacts/testvm/omarchy/seed.iso"

        with mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.omarchy, "create_cidata_iso", return_value=seed), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            exit_code = vmctl.lifecycle.cmd_install_omarchy(args)

        self.assertEqual(exit_code, 0)
        qemu_cmd = run_cmd.call_args.args[0]
        self.assertIn("-cdrom", qemu_cmd)
        self.assertIn(str(self.root / self.vm_config["iso"]), qemu_cmd)
        self.assertIn(f"file={seed},format=raw,if=virtio,media=cdrom,readonly=on", qemu_cmd)
        self.assertIn("-no-reboot", qemu_cmd)


if __name__ == "__main__":
    unittest.main()
