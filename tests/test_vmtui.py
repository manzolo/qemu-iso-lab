import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VMTUI_PATH = ROOT / "bin" / "vmtui"


class VmtuiTests(unittest.TestCase):
    def setUp(self):
        # A throwaway repository root: vmtui resolves every relative profile
        # path (isos/, artifacts/, PID files) against VMTUI_ROOT_DIR, so the
        # state of the developer's host never leaks into the menu facts. The
        # code itself is reached through symlinks to the real checkout.
        self.tempdir = tempfile.TemporaryDirectory()
        self.bindir = Path(self.tempdir.name)
        (self.bindir / "vmctl").symlink_to(ROOT / "vmctl", target_is_directory=True)
        (self.bindir / "bin").symlink_to(ROOT / "bin", target_is_directory=True)
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
                    "ssh_target": "lab@host.lan",
                    "project_dir": "/home/lab/qemu-iso-lab",
                    "local_spice_port": 5930,
                    "remote_spice_port": 5930,
                }
            }
        }
        (self.config_dir / "remotes.json").write_text(json.dumps(remotes), encoding="utf-8")
        dialog = self.bindir / "dialog"
        dialog.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        dialog.chmod(0o755)
        # Built from scratch rather than copied from os.environ: no inherited
        # VMTUI_* knobs, no ~/.profile PATH additions, no host fzf.
        self.env = {
            "PATH": os.pathsep.join([str(self.bindir), str(Path(sys.executable).parent), "/usr/local/bin", os.defpath]),
            "HOME": str(self.bindir / "home"),
            "LC_ALL": "C.UTF-8",
            "TERM": "dumb",
            "VMTUI_TEST_MODE": "1",
            "VMTUI_UI": "dialog",
            "VMTUI_ROOT_DIR": str(self.bindir),
            "VMTUI_CONFIG_DIR": str(self.config_dir),
            "VMTUI_STATE_DIR": str(self.bindir / "state"),
        }
        (self.bindir / "home").mkdir()

    def mark_installed(self, vm_name: str) -> None:
        # enough allocated data to count as an installed OS (> 16 MiB)
        disk = self.bindir / "artifacts" / vm_name / "disk.qcow2"
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(b"\x01" * (24 * 1024 * 1024))

    def mark_prepared(self, vm_name: str) -> None:
        # a freshly created, still empty image: a sparse file with no allocated
        # blocks, so the test does not need qemu-img (the CI test job has none)
        disk = self.bindir / "artifacts" / vm_name / "disk.qcow2"
        disk.parent.mkdir(parents=True, exist_ok=True)
        with disk.open("wb") as handle:
            handle.truncate(1024 * 1024 * 1024)

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
        self.assertNotIn("__sep_OTHER", output)
        # Back is the last selectable row
        self.assertEqual(output[-2], "Back")

    def test_unified_menu_for_autoinstall_plus_cloud_init_vm(self):
        output = self._unified_menu("ubuntu-niri")
        self.assertIn("Full Bootstrap", output)
        self.assertIn("Unattended Install", output)
        self.assertIn("Cloud-Init Flow", output)
        self.assertIn("Post-Install", output)
        # ubuntu-niri has no disk in the test tree: RUN hides boot entries and
        # the first install action is the suggested one
        self.assertNotIn("Boot Desktop", output)
        self.assertNotIn("Boot Headless", output)
        self.assertNotIn("First Boot", output)
        self.assertTrue(self._description_of(output, "Full Bootstrap").startswith("▶ "))

    def test_unified_menu_installed_vm_shows_run_entries(self):
        self.mark_installed("test-ssh")
        output = self._unified_menu("test-ssh")
        self.assertIn("Boot Desktop", output)
        self.assertIn("Boot Headless", output)
        self.assertIn("SSH Console", output)
        self.assertNotIn("Stop VM", output)  # not running
        self.assertTrue(self._description_of(output, "Boot Desktop").startswith("▶ "))
        self.assertTrue(self._description_of(output, "Boot Headless").startswith("  "))

    def test_running_vm_on_empty_disk_still_offers_stop_and_ssh(self):
        # installer in progress: running=1, prepared=1, installed=0
        self.mark_prepared("test-ssh")
        script = (
            "source bin/vmtui; load_vm_facts test-ssh; "
            "FACTS[running]=1; FACTS[runtime]=tracked:4242; RECOMMENDED=$(recommended_action); "
            "echo \"$RECOMMENDED\"; build_vm_menu_items"
        )
        result = self.run_bash(script)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "SSH Console")
        menu = lines[1:]
        self.assertIn("Stop VM", menu)
        self.assertIn("SSH Console", menu)
        self.assertNotIn("Boot Headless", menu)
        self.assertTrue(self._description_of(menu, "SSH Console").startswith("▶ "))
        # the recommended entry always exists in the menu it is rendered for
        self.assertIn(lines[0], menu)

    def test_recommended_action_follows_state(self):
        result = self.run_bash("source bin/vmtui; load_vm_facts test-ssh; recommended_action")
        self.assertEqual(result.stdout.strip(), "Guided Provision")
        self.mark_installed("test-ssh")
        result = self.run_bash("source bin/vmtui; load_vm_facts test-ssh; recommended_action")
        self.assertEqual(result.stdout.strip(), "Boot Desktop")
        # state-independent: the flow's main install action for an Arch profile
        result = self.run_bash("source bin/vmtui; load_vm_facts arch-noctalia-local; primary_install_action")
        self.assertEqual(result.stdout.strip(), "Arch Bootstrap")

    def test_vm_facts_exposes_flags(self):
        result = self.run_bash("source bin/vmtui; vm_facts test-ssh")
        facts = dict(line.split("\t", 1) for line in result.stdout.splitlines())
        self.assertEqual(facts["has_ssh"], "1")
        self.assertEqual(facts["ssh_port"], "2293")
        self.assertEqual(facts["installed"], "0")
        self.assertEqual(facts["running"], "0")
        self.assertEqual(facts["video_default"], "std")
        self.assertEqual(facts["label"], "Test SSH VM")

    def test_prepared_empty_disk_is_not_installed(self):
        self.mark_prepared("test-ssh")
        result = self.run_bash("source bin/vmtui; vm_status_summary test-ssh")
        self.assertEqual(result.stdout.strip(), "prepared, empty disk")
        result = self.run_bash("source bin/vmtui; load_vm_facts test-ssh; recommended_action; vm_status_header")
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Guided Provision")
        self.assertTrue(lines[1].startswith("□ disk prepared but empty"))
        output = self._unified_menu("test-ssh")
        self.assertNotIn("Boot Desktop", output)
        result = self.run_bash("source bin/vmtui; list_dashboard_items installed ''")
        self.assertTrue(self._description_of(result.stdout.splitlines()[1:], "test-ssh").startswith("□ test-ssh"))
        self.assertIn("empty disk", result.stdout)

    def test_state_key_is_a_safe_single_path_component(self):
        result = self.run_bash(
            "source bin/vmtui; state_key ubuntu24.04; state_key '../../../.bashrc'; state_key 'a/b'; state_key 'a_b'; state_key ''"
        )
        keys = result.stdout.splitlines()
        self.assertEqual(keys[0], "ubuntu24.04")
        self.assertRegex(keys[1], r"^_\.\._\.\._\.bashrc-\d+$")
        self.assertRegex(keys[2], r"^a_b-\d+$")
        self.assertEqual(keys[3], "a_b")
        self.assertRegex(keys[4], r"^_-\d+$")
        self.assertEqual(len(set(keys)), len(keys))
        for key in keys:
            self.assertNotIn("/", key)
            self.assertFalse(key.startswith("."))
        result = self.run_bash(
            "source bin/vmtui; video_pref_set '../../escape' std; find \"$VMTUI_STATE_DIR\" -type f"
        )
        files = result.stdout.splitlines()
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith(self.env["VMTUI_STATE_DIR"] + "/video/"), files[0])

    def test_fzf_is_usable_requires_minimum_version(self):
        fake = self.bindir / "fzf"
        fake.write_text("#!/usr/bin/env sh\necho '0.44.1 (debian)'\n", encoding="utf-8")
        fake.chmod(0o755)
        # auto-detection: no VMTUI_UI in the environment
        auto_env = {key: value for key, value in self.env.items() if key != "VMTUI_UI"}
        result = subprocess.run(["bash", "-lc", "source bin/vmtui; echo $UI_BACKEND"], cwd=ROOT, env=auto_env,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "fzf")
        fake.write_text("#!/usr/bin/env sh\necho '0.29.0 (debian)'\n", encoding="utf-8")
        result = subprocess.run(["bash", "-lc", "source bin/vmtui; echo $UI_BACKEND"], cwd=ROOT, env=auto_env,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "dialog")
        env = dict(self.env, VMTUI_UI="fzf")
        result = subprocess.run(["bash", "-lc", "source bin/vmtui"], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("0.36.0", result.stderr)

    def test_vm_status_header_installed(self):
        self.mark_installed("test-ssh")
        result = self.run_bash("source bin/vmtui; load_vm_facts test-ssh; vm_status_header")
        self.assertTrue(result.stdout.startswith("■ stopped, disk has data"))
        self.assertIn("SSH port 2293", result.stdout)

    def test_dashboard_summary_and_rows(self):
        self.mark_installed("test-ssh")
        result = self.run_bash("source bin/vmtui; list_dashboard_items all ''")
        lines = result.stdout.splitlines()
        summary = lines[0].split("\t")
        self.assertEqual(summary[0], "__summary")
        total, installed, running, shown = map(int, summary[1:])
        self.assertEqual(total, shown)
        self.assertGreaterEqual(installed, 1)
        self.assertGreaterEqual(running, 0)
        tags = lines[1::2]
        self.assertIn("test-ssh", tags)
        row = self._description_of(lines[1:], "test-ssh")
        self.assertTrue(row.startswith("■ test-ssh"), row)
        self.assertIn(":2293", row)
        # installed VMs sort before the rest: no ○ row may precede a ■ row
        glyphs = [lines[1:][i + 1][0] for i, tag in enumerate(lines[1:]) if tag in tags and i % 2 == 0]
        self.assertGreater(glyphs.index("○") if "○" in glyphs else len(glyphs), glyphs.index("■"))

    def test_dashboard_filters(self):
        result = self.run_bash("source bin/vmtui; list_dashboard_items find niri")
        tags = result.stdout.splitlines()[1::2]
        self.assertIn("ubuntu-niri", tags)
        self.assertNotIn("alpine-ci", tags)
        result = self.run_bash("source bin/vmtui; list_dashboard_items family alpine")
        tags = result.stdout.splitlines()[1::2]
        self.assertIn("alpine-ci", tags)
        self.assertNotIn("ubuntu-niri", tags)
        result = self.run_bash("source bin/vmtui; list_family_menu_items")
        self.assertIn("alpine", result.stdout.splitlines()[0::2])

    def test_video_preference_is_remembered_and_validated(self):
        result = self.run_bash(
            "source bin/vmtui; current_vm=test-ssh; load_vm_facts test-ssh; "
            "video_args | wc -l; video_pref_set test-ssh std; video_args; "
            "video_pref_set test-ssh bogus; video_args | wc -l; video_pref_clear test-ssh; video_args | wc -l"
        )
        self.assertEqual(result.stdout.split("\n")[:5], ["0", "--video", "std", "0", "0"])

    def test_unified_menu_for_arch_bootstrap_vm(self):
        # SSH Console is offered only once the disk holds an OS
        self.mark_installed("arch-noctalia-local")
        output = self._unified_menu("arch-noctalia-local")
        self.assertIn("Arch Bootstrap", output)
        self.assertIn("Arch Install (Interactive)", output)
        self.assertIn("SSH Console", output)
        self.assertNotIn("Full Bootstrap", output)
        self.assertNotIn("Debian Preseed Bootstrap", output)

    def test_unified_menu_for_omarchy_bootstrap_vm(self):
        self.mark_installed("arch-omarchy-nvidia-local")
        output = self._unified_menu("arch-omarchy-nvidia-local")
        self.assertIn("Omarchy Bootstrap", output)
        self.assertIn("Omarchy Unattended Install", output)
        self.assertIn("SSH Console", output)
        self.assertNotIn("Arch Bootstrap", output)

    def test_unified_menu_for_preseed_vm(self):
        output = self._unified_menu("debian-server")
        self.assertIn("Debian Preseed Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("Post-Install", output)
        self.assertNotIn("Arch Bootstrap", output)

    def test_unified_menu_for_kickstart_vm(self):
        output = self._unified_menu("almalinux-server")
        self.assertIn("Kickstart Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("Post-Install", output)

    def test_unified_menu_for_alpine_vm(self):
        output = self._unified_menu("alpine-niri")
        self.assertIn("Alpine Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("Post-Install", output)
        self.assertNotIn("Kickstart Bootstrap", output)
        # no disk yet: the bootstrap is the suggested first step
        result = self.run_bash("source bin/vmtui; load_vm_facts alpine-niri; recommended_action")
        self.assertEqual(result.stdout.strip(), "Alpine Bootstrap")

    def test_unified_menu_for_fedora_dms_vm_uses_kickstart(self):
        output = self._unified_menu("fedora-niri-dms-local")
        self.assertIn("Kickstart Bootstrap", output)
        self.assertNotIn("Alpine Bootstrap", output)

    def test_unified_menu_for_plain_vm_hides_inapplicable_entries(self):
        output = self._unified_menu("alpine-ci")
        self.assertNotIn("Full Bootstrap", output)
        self.assertNotIn("Arch Bootstrap", output)
        self.assertIn("Guided Provision", output)
        self.assertIn("Installer Only", output)
        self.assertNotIn("SSH Console", output)
        self.assertNotIn("Post-Install", output)
        self.assertNotIn("Stop VM", output)
        self.assertIn("Video Profile", output)

    def test_unified_menu_for_ssh_only_vm_shows_active_ssh(self):
        self.mark_installed("test-ssh")
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
        # freebsd has no disk in the temp root (plain VM, never installed)
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
        for sep in ("__sep_RUN", "__sep_MAINTENANCE", "__sep_ADVANCED"):
            result = self.run_bash(f"source bin/vmtui; resolve_action {sep}")
            self.assertEqual(result.stdout.strip(), "noop", f"separator {sep} not mapped to noop")

    def test_resolve_action_maps_remote_hosts(self):
        result = self.run_bash("source bin/vmtui; resolve_action 'Remote Hosts'")
        self.assertEqual(result.stdout.strip(), "remote-hosts")

    def test_resolve_action_maps_bootstrap_entries(self):
        for entry, expected in (
            ("Full Bootstrap", "bootstrap-unattended"),
            ("Arch Bootstrap", "bootstrap-archinstall"),
            ("Omarchy Bootstrap", "bootstrap-omarchy"),
            ("Omarchy Unattended Install", "install-omarchy"),
            ("Debian Preseed Bootstrap", "bootstrap-preseed"),
            ("Kickstart Bootstrap", "bootstrap-kickstart"),
            ("Alpine Bootstrap", "bootstrap-alpine"),
            ("Unattended Install", "full-auto-install"),
            ("Cloud-Init Flow", "cloud-init-install"),
            ("Flash Empty Disk", "flash"),
            ("Force Flash", "flash-force"),
            ("Import Disk", "import-device"),
            ("Video Profile", "video-profile"),
        ):
            result = self.run_bash(f"source bin/vmtui; resolve_action {entry!r}")
            self.assertEqual(result.stdout.strip(), expected, f"{entry!r} did not resolve to {expected}")

    def test_list_remote_menu_items_reads_remotes_json(self):
        result = self.run_bash("source bin/vmtui; list_remote_menu_items")
        output = result.stdout.splitlines()
        self.assertEqual(output[0], "i9")
        self.assertIn("lab@host.lan", output[1])
        self.assertIn("5930->5930", output[1])

    def test_install_command_for_remote_viewer_detects_apt(self):
        apt_get = self.bindir / "apt-get"
        apt_get.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        apt_get.chmod(0o755)
        result = self.run_bash("source bin/vmtui; install_command_for remote-viewer")
        self.assertEqual(result.stdout.strip(), "sudo apt-get install -y virt-viewer")

    def test_ui_backend_env_override(self):
        env = dict(self.env, VMTUI_UI="dialog")
        result = subprocess.run(["bash", "-lc", "source bin/vmtui; echo $UI_BACKEND"], cwd=ROOT, env=env,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "dialog")
        env = dict(self.env, VMTUI_UI="bogus")
        result = subprocess.run(["bash", "-lc", "source bin/vmtui"], cwd=ROOT, env=env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("VMTUI_UI", result.stderr)

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
