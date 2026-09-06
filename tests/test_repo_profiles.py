import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.config  # noqa: E402
import vmctl.state  # noqa: E402


class RepositoryProfileCatalogTests(unittest.TestCase):
    def test_repository_catalog_contains_first_wave_profiles(self):
        # Validate the tracked catalog alone: the developer's gitignored
        # vms/profiles/local.json must not be able to change this outcome.
        original_root = vmctl.state.ROOT
        original_config_dir = vmctl.state.CONFIG_DIR
        with tempfile.TemporaryDirectory() as tmp:
            profiles_dir = Path(tmp) / "vms" / "profiles"
            profiles_dir.mkdir(parents=True)
            for profile_path in (ROOT / "vms" / "profiles").glob("*.json"):
                if profile_path.name != "local.json":
                    shutil.copy(profile_path, profiles_dir / profile_path.name)
            try:
                vmctl.state.ROOT = Path(tmp)
                vmctl.state.CONFIG_DIR = Path(tmp) / "vms"
                cfg = vmctl.config.load_config()
            finally:
                vmctl.state.ROOT = original_root
                vmctl.state.CONFIG_DIR = original_config_dir

        for profile in (
            "alpine-installed-ci",
            "debian-efi",
            "debian-bios",
            "ubuntu-server-headless",
            "fedora-server-efi",
            "freebsd",
            "arch-omarchy-nvidia-local",
            "fedora-niri-dms-local",
            "alpine-niri",
            "cachyos-nvidia-local",
            "windows11-unattended",
            "windows10-unattended",
        ):
            self.assertIn(profile, cfg["vms"])

        # Windows rides bootstrap-windows: generic identity, virtio disk (viostor is injected
        # in WinPE), OpenSSH post-install, no download URL (Microsoft publishes none).
        win = cfg["vms"]["windows11-unattended"]
        self.assertEqual(win["windows_config"]["username"], "lab")
        self.assertEqual(win["ssh_provision"]["user"], "lab")
        self.assertEqual(win["disk"]["interface"], "virtio")
        self.assertEqual(win["windows_config"]["driver_flavor"], "w11")
        self.assertNotIn("iso_url", win)
        self.assertEqual(cfg["vms"]["windows11-template"]["meta"]["role"], "import-template")
        w10 = cfg["vms"]["windows10-unattended"]
        self.assertEqual(w10["windows_config"]["driver_flavor"], "w10")
        self.assertEqual(w10["windows_config"]["edition"], "Windows 10 Pro")
        self.assertFalse(w10["windows_config"]["bypass_requirements"])
        self.assertNotEqual(w10["ssh_provision"]["ssh_host_port"], win["ssh_provision"]["ssh_host_port"])
        for profile in (win, w10):  # first shutdown commits feature operations: far longer than 60 s
            self.assertGreaterEqual(profile["acpi_poweroff_grace_sec"], 300)
            self.assertEqual(profile["shared_dir"], {"source": "shared", "tag": "shared"})

        # CachyOS rides the Arch pacstrap flow on its own archiso: kernel paths,
        # serial prompts and the live pacman.conf must all be declared.
        for profile in ("cachyos-local", "cachyos-nvidia-local"):
            vm = cfg["vms"][profile]
            self.assertEqual(vm["installer_boot"]["kernel"], "arch/boot/x86_64/vmlinuz-linux-cachyos")
            arch_cfg = vm["archinstall_config"]
            self.assertEqual(arch_cfg["kernels"], ["linux-cachyos"])
            self.assertTrue(arch_cfg["inherit_live_pacman_conf"])
            self.assertEqual(arch_cfg["live_login_prompt"], "CachyOS login:")
            self.assertEqual(arch_cfg["live_shell_prompt"], "root@CachyOS")
            self.assertIn("cachyos-keyring", arch_cfg["packages"])


if __name__ == "__main__":
    unittest.main()
