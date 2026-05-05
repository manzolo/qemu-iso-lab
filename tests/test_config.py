import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._common import BaseVmctlTestCase  # noqa: E402


class ConfigTests(BaseVmctlTestCase):
    def test_load_config_reads_profiles_from_config_dir(self):
        config = self.vmctl.load_config()

        self.assertIn(self.vm_name, config["vms"])
        self.assertNotIn("catalog", config)

    def test_load_config_rejects_profile_missing_required_field(self):
        del self.vm_config["memory_mb"]
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("memory_mb", str(ctx.exception))

    def test_load_config_rejects_invalid_firmware_type(self):
        self.vm_config["firmware"] = {"type": "tianocore"}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("firmware.type", str(ctx.exception))

    def test_load_config_rejects_efi_firmware_missing_paths(self):
        self.vm_config["firmware"] = {"type": "efi"}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        message = str(ctx.exception)
        self.assertIn("firmware.code", message)
        self.assertIn("firmware.vars_template", message)
        self.assertIn("firmware.vars_path", message)

    def test_load_config_rejects_video_default_not_in_variants(self):
        self.vm_config["video"] = {"default": "ghost", "variants": {"std": []}}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("video.default", str(ctx.exception))

    def test_load_config_rejects_installer_order_with_unknown_variant(self):
        self.vm_config["video"]["installer_order"] = ["std", "ghost"]
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("installer_order", str(ctx.exception))

    def test_load_config_aggregates_multiple_errors_in_one_message(self):
        del self.vm_config["memory_mb"]
        self.vm_config["firmware"] = {"type": "uefi"}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        message = str(ctx.exception)
        self.assertIn("memory_mb", message)
        self.assertIn("firmware.type", message)

    def test_load_config_reads_local_profile_override_file(self):
        local_vm = json.loads(json.dumps(self.vm_config))
        local_vm["name"] = "Local VM"
        local_vm["disk"]["path"] = "artifacts/localvm/disk.qcow2"
        self.write_extra_profile("local.json", {"vms": {"localvm": local_vm}})

        config = self.vmctl.load_config()

        self.assertIn("localvm", config["vms"])
        self.assertEqual(config["vms"]["localvm"]["name"], "Local VM")

    def test_load_config_local_profile_can_override_shared_profile(self):
        self.vm_config["ssh_provision"] = {
            "hostname": "base-vm",
            "user": "vmuser",
            "ssh_host_port": 2222,
            "post_install_run": ["echo base"],
        }
        self.write_config_dir()

        local_vm = {
            "name": "Local Override",
            "ssh_provision": {
                "ssh_key": "~/.ssh/id_rsa",
                "copy_from_host": [{"source": "~/.config/app/", "dest": "/home/vmuser/.config/app"}],
            },
        }
        self.write_extra_profile("local.json", {"vms": {self.vm_name: local_vm}})

        config = self.vmctl.load_config()

        vm = config["vms"][self.vm_name]
        self.assertEqual(vm["name"], "Local Override")
        self.assertEqual(vm["ssh_provision"]["hostname"], "base-vm")
        self.assertEqual(vm["ssh_provision"]["ssh_key"], "~/.ssh/id_rsa")
        self.assertEqual(vm["ssh_provision"]["post_install_run"], ["echo base"])

    def test_load_config_rejects_duplicate_shared_profile(self):
        duplicate_vm = json.loads(json.dumps(self.vm_config))
        self.write_extra_profile("z-duplicate.json", {"vms": {self.vm_name: duplicate_vm}})

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("Duplicate VM profile", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
