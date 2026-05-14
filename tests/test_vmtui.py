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
                    "disk": {
                        "path": "artifacts/test-ssh/disk.qcow2",
                        "size": "16G",
                        "format": "qcow2",
                        "interface": "virtio",
                    },
                    "firmware": {
                        "type": "efi",
                        "code": "/usr/share/OVMF/OVMF_CODE_4M.fd",
                        "vars_template": "/usr/share/OVMF/OVMF_VARS_4M.fd",
                        "vars_path": "artifacts/test-ssh/OVMF_VARS.fd",
                    },
                    "memory_mb": 2048,
                    "cpus": 2,
                    "network": "user",
                    "video": {
                        "default": "std",
                        "variants": {"std": ["-vga", "std", "-display", "gtk"]},
                    },
                    "ssh_provision": {
                        "hostname": "test-ssh",
                        "user": "tester",
                        "ssh_key": "~/.ssh/id_ed25519",
                        "ssh_host_port": 2293,
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

    def _unified_menu(self, vm_name: str) -> list[str]:
        result = self.run_bash(f"source bin/vmtui; list_vm_menu_items_unified {vm_name}")
        return result.stdout.splitlines()

    def _description_of(self, output: list[str], tag: str) -> str:
        for i, line in enumerate(output):
            if line == tag and i + 1 < len(output):
                return output[i + 1]
        return ""

    def test_unified_menu_has_all_sections(self):
        output = self._unified_menu("ubuntu-niri")
        self.assertIn("__sep_INSTALL", output)
        self.assertIn("__sep_RUN", output)
        self.assertIn("__sep_MAINTENANCE", output)
        self.assertIn("__sep_ADVANCED", output)
        self.assertIn("__sep_OTHER", output)
        self.assertIn("Back", output)

    def test_unified_menu_for_autoinstall_plus_cloud_init_vm(self):
        output = self._unified_menu("ubuntu-niri")
        self.assertIn("Full Bootstrap", output)
        self.assertIn("Unattended Install", output)
        self.assertIn("Cloud-Init Flow", output)
        self.assertIn("Boot Desktop", output)
        self.assertIn("Boot Headless", output)
        self.assertIn("SSH Console", output)
        self.assertIn("Post-Install", output)
        self.assertIn("First Boot", output)

    def test_unified_menu_for_arch_bootstrap_vm(self):
        output = self._unified_menu("arch-noctalia-local")
        self.assertIn("Arch Bootstrap", output)
        self.assertIn("Arch Install (Interactive)", output)
        self.assertIn("SSH Console", output)
        self.assertNotIn("Full Bootstrap", output)
        self.assertNotIn("Debian Preseed Bootstrap", output)

    def test_unified_menu_for_preseed_vm(self):
        output = self._unified_menu("debian-server")
        self.assertIn("Debian Preseed Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("SSH Console", output)
        self.assertNotIn("Arch Bootstrap", output)

    def test_unified_menu_for_kickstart_vm(self):
        output = self._unified_menu("almalinux-server")
        self.assertIn("Kickstart Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("SSH Console", output)

    def test_unified_menu_for_plain_vm_uses_na_badges(self):
        output = self._unified_menu("alpine-ci")
        self.assertNotIn("Full Bootstrap", output)
        self.assertNotIn("Arch Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("Installer Only", output)
        self.assertIn("SSH Console", output)
        self.assertIn(
            "(no ssh_provision in profile)",
            self._description_of(output, "SSH Console"),
        )
        self.assertIn("Stop VM", output)
        self.assertIn(
            "(VM not running)",
            self._description_of(output, "Stop VM"),
        )

    def test_unified_menu_for_ssh_only_vm_shows_active_ssh(self):
        output = self._unified_menu("test-ssh")
        self.assertIn("SSH Console", output)
        self.assertIn(
            "Open a shell inside the VM",
            self._description_of(output, "SSH Console"),
        )
        self.assertIn("Post-Install", output)
        self.assertIn(
            "Run configured SSH provisioning tasks",
            self._description_of(output, "Post-Install"),
        )

    def test_unified_menu_includes_advanced_entries(self):
        output = self._unified_menu("alpine-ci")
        self.assertIn("Flash Empty Disk", output)
        self.assertIn("Force Flash", output)
        self.assertIn("Import Disk", output)

    def test_unified_menu_includes_maintenance_entries(self):
        output = self._unified_menu("alpine-ci")
        self.assertIn("Boot Check", output)
        self.assertIn("Clean VM", output)
        self.assertIn("Delete ISO", output)
        self.assertIn("Profile Details", output)
        self.assertIn("Fetch ISO", output)
        self.assertIn("Prepare VM", output)

    def test_is_na_action_ssh_when_no_ssh_provision(self):
        result = subprocess.run(
            ["bash", "-lc", "source bin/vmtui; is_na_action 'SSH Console' alpine-ci"],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_is_na_action_ssh_when_ssh_provision_present(self):
        result = subprocess.run(
            ["bash", "-lc", "source bin/vmtui; is_na_action 'SSH Console' test-ssh"],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_is_na_action_boot_desktop_when_disk_missing(self):
        # freebsd has no disk.qcow2 in artifacts/ (plain VM, never installed)
        result = subprocess.run(
            ["bash", "-lc", "source bin/vmtui; is_na_action 'Boot Desktop' freebsd"],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_vm_status_summary_not_installed_when_no_disk(self):
        result = self.run_bash("source bin/vmtui; vm_status_summary freebsd")
        self.assertEqual(result.stdout.strip(), "not installed")

    def test_resolve_action_separator_returns_noop(self):
        result = self.run_bash("source bin/vmtui; resolve_action __sep_INSTALL")
        self.assertEqual(result.stdout.strip(), "noop")

    def test_resolve_action_other_separators_also_noop(self):
        for sep in ("__sep_RUN", "__sep_MAINTENANCE", "__sep_ADVANCED", "__sep_OTHER"):
            result = self.run_bash(f"source bin/vmtui; resolve_action {sep}")
            self.assertEqual(result.stdout.strip(), "noop", f"separator {sep} not mapped to noop")

    def test_resolve_action_maps_remote_hosts(self):
        result = self.run_bash("source bin/vmtui; resolve_action 'Remote Hosts'")
        self.assertEqual(result.stdout.strip(), "remote-hosts")

    def test_resolve_action_maps_bootstrap_entries(self):
        for entry, expected in (
            ("Full Bootstrap", "bootstrap-unattended"),
            ("Arch Bootstrap", "bootstrap-archinstall"),
            ("Debian Preseed Bootstrap", "bootstrap-preseed"),
            ("Kickstart Bootstrap", "bootstrap-kickstart"),
            ("Unattended Install", "full-auto-install"),
            ("Cloud-Init Flow", "cloud-init-install"),
            ("Flash Empty Disk", "flash"),
            ("Force Flash", "flash-force"),
            ("Import Disk", "import-device"),
        ):
            result = self.run_bash(f"source bin/vmtui; resolve_action {entry!r}")
            self.assertEqual(result.stdout.strip(), expected, f"{entry!r} did not resolve to {expected}")

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

    def test_list_vm_menu_items_main_lists_profiles(self):
        result = self.run_bash("source bin/vmtui; list_vm_menu_items")
        output = result.stdout.splitlines()
        # tags are even-indexed lines (0, 2, 4, ...)
        tags = output[::2]
        self.assertIn("alpine-ci", tags)
        self.assertIn("ubuntu-niri", tags)
        self.assertIn("arch-noctalia-local", tags)


if __name__ == "__main__":
    unittest.main()
