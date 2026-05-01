import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VMTUI_PATH = ROOT / "bin" / "vmtui"


class VmtuiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.bindir = Path(self.tempdir.name)
        self.config_dir = self.bindir / "vms"
        profiles_dir = self.config_dir / "profiles"
        profiles_dir.mkdir(parents=True)
        for profile_path in sorted((ROOT / "vms" / "profiles").glob("*.json")):
            if profile_path.name == "local.json":
                continue
            (profiles_dir / profile_path.name).write_text(profile_path.read_text(encoding="utf-8"), encoding="utf-8")
        ssh_vm = {
            "vms": {
                "test-ssh": {
                    "name": "Test SSH VM",
                    "iso": "isos/test.iso",
                    "disk": {"path": "artifacts/test-ssh/disk.qcow2", "size": "16G"},
                    "firmware": {"type": "efi"},
                    "memory_mb": 2048,
                    "cpus": 2,
                    "ssh_provision": {
                        "hostname": "test-ssh",
                        "user": "tester",
                        "ssh_key": "~/.ssh/id_ed25519",
                        "ssh_host_port": 2223,
                    },
                }
            }
        }
        (profiles_dir / "test-ssh.json").write_text(json.dumps(ssh_vm), encoding="utf-8")
        remotes = {
            "remotes": {
                "i9": {
                    "label": "i9.lan",
                    "ssh_target": "manzolo@i9.lan",
                    "project_dir": "/home/manzolo/Workspaces/qemu/qemu-iso-lab",
                    "local_spice_port": 5930,
                    "remote_spice_port": 5930,
                }
            }
        }
        (self.config_dir / "remotes.json").write_text(json.dumps(remotes), encoding="utf-8")
        dialog = self.bindir / "dialog"
        dialog.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        dialog.chmod(0o755)
        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.bindir}:{self.env['PATH']}"
        self.env["VMTUI_TEST_MODE"] = "1"
        self.env["VMTUI_CONFIG_DIR"] = str(self.config_dir)

    def tearDown(self):
        self.tempdir.cleanup()

    def run_bash(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-lc", script],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_list_vm_action_items_uses_compact_grouped_menu(self):
        result = self.run_bash("source bin/vmtui; list_vm_action_items ubuntu-niri")
        output = result.stdout.splitlines()

        self.assertEqual(output[:2], ["Start Here", "Recommended flows for this VM"])
        self.assertEqual(output[-4:], ["Profile Details", "Review this VM configuration", "Main Menu", "Return to the main menu"])
        self.assertIn("Start Here", output)
        self.assertIn("Installation", output)
        self.assertIn("Run & Access", output)
        self.assertIn("Maintenance", output)
        self.assertNotIn("start", output)
        self.assertNotIn("shell", output)
        self.assertNotIn("clean", output)

    def test_list_quick_action_items_include_bootstrap_for_supported_vm(self):
        result = self.run_bash("source bin/vmtui; list_quick_action_items ubuntu-niri")
        output = result.stdout.splitlines()

        self.assertIn("Full Bootstrap", output)
        self.assertIn("Cloud-Init Flow", output)
        self.assertIn("Boot Desktop", output)
        self.assertIn("Boot Headless", output)
        self.assertIn("SSH Console", output)

    def test_list_quick_action_items_fallback_for_plain_vm(self):
        result = self.run_bash("source bin/vmtui; list_quick_action_items alpine-ci")
        output = result.stdout.splitlines()

        self.assertNotIn("Full Bootstrap", output)
        self.assertIn("Guided Install", output)
        self.assertIn("Boot Desktop", output)
        self.assertIn("Boot Headless", output)
        self.assertNotIn("SSH Console", output)

    def test_list_quick_action_items_include_shell_for_ssh_provision_vm(self):
        result = self.run_bash("source bin/vmtui; list_quick_action_items test-ssh")
        output = result.stdout.splitlines()

        self.assertIn("Guided Install", output)
        self.assertIn("Boot Desktop", output)
        self.assertIn("Boot Headless", output)
        self.assertIn("SSH Console", output)
        self.assertNotIn("Full Bootstrap", output)
        self.assertNotIn("Cloud-Init Flow", output)

    def test_list_install_action_items_include_cloud_init_entries_for_supported_vm(self):
        result = self.run_bash("source bin/vmtui; list_install_action_items ubuntu-niri")
        output = result.stdout.splitlines()

        self.assertIn("Guided Provision", output)
        self.assertIn("Autoinstall", output)
        self.assertIn("Cloud-Init Flow", output)
        self.assertIn("Seeded Installer", output)
        self.assertNotIn("Full Bootstrap", output)

    def test_list_install_action_items_omits_cloud_init_entries_for_plain_vm(self):
        result = self.run_bash("source bin/vmtui; list_install_action_items alpine-ci")
        output = result.stdout.splitlines()

        self.assertIn("Guided Provision", output)
        self.assertNotIn("Autoinstall", output)
        self.assertNotIn("Cloud-Init Flow", output)
        self.assertNotIn("Seeded Installer", output)

    def test_list_run_action_items_include_cloud_init_entries_for_supported_vm(self):
        result = self.run_bash("source bin/vmtui; list_run_action_items ubuntu-niri")
        output = result.stdout.splitlines()

        self.assertIn("Boot Desktop", output)
        self.assertIn("Boot Headless", output)
        self.assertIn("Remote SPICE", output)
        self.assertIn("Stop VM", output)
        self.assertIn("SSH Console", output)
        self.assertIn("First Boot", output)
        self.assertIn("Post-Install", output)

    def test_list_run_action_items_omit_cloud_init_entries_for_plain_vm(self):
        result = self.run_bash("source bin/vmtui; list_run_action_items alpine-ci")
        output = result.stdout.splitlines()

        self.assertIn("Boot Desktop", output)
        self.assertIn("Boot Headless", output)
        self.assertIn("Remote SPICE", output)
        self.assertIn("Stop VM", output)
        self.assertNotIn("SSH Console", output)
        self.assertNotIn("First Boot", output)
        self.assertNotIn("Post-Install", output)

    def test_list_run_action_items_include_ssh_post_install_for_ssh_provision_vm(self):
        result = self.run_bash("source bin/vmtui; list_run_action_items test-ssh")
        output = result.stdout.splitlines()

        self.assertIn("Boot Desktop", output)
        self.assertIn("Boot Headless", output)
        self.assertIn("Remote SPICE", output)
        self.assertIn("Stop VM", output)
        self.assertIn("SSH Console", output)
        self.assertIn("Post-Install", output)
        self.assertNotIn("First Boot", output)

    def test_list_remote_menu_items_reads_remotes_json(self):
        result = self.run_bash("source bin/vmtui; list_remote_menu_items")
        output = result.stdout.splitlines()

        self.assertEqual(output[0], "i9")
        self.assertIn("manzolo@i9.lan", output[1])
        self.assertIn("5930->5930", output[1])

    def test_install_command_for_remote_viewer_detects_apt(self):
        apt_get = self.bindir / "apt-get"
        apt_get.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        apt_get.chmod(0o755)

        result = self.run_bash("source bin/vmtui; install_command_for remote-viewer")

        self.assertEqual(result.stdout.strip(), "sudo apt-get install -y virt-viewer")

    def test_resolve_action_maps_remote_hosts(self):
        result = self.run_bash("source bin/vmtui; resolve_action 'Remote Hosts'")

        self.assertEqual(result.stdout.strip(), "remote-hosts")

    def test_list_maintenance_action_items_include_boot_check_and_clean(self):
        result = self.run_bash("source bin/vmtui; list_maintenance_action_items")
        output = result.stdout.splitlines()

        self.assertIn("Boot Check", output)
        self.assertIn("Clean VM", output)
        self.assertIn("Delete ISO", output)


if __name__ == "__main__":
    unittest.main()
