import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.autoyast  # noqa: E402
import vmctl.config  # noqa: E402
import vmctl.kickstart  # noqa: E402
import vmctl.netlab  # noqa: E402
import vmctl.state  # noqa: E402
import vmctl.windows  # noqa: E402


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
            "pfsense-lab",
            "pihole-lab",
            "lubuntu22-lab",
            "windows7-unattended",
            "lubuntu-24.04",
            "kubuntu-24.04",
            "xubuntu-24.04",
            "ubuntu-mate-24.04",
            "ubuntu-budgie-24.04",
            "rocky9",
            "fedora-silverblue",
            "opensuse-tumbleweed-autoyast",
        ):
            self.assertIn(profile, cfg["vms"])

        # Every tracked profile that provisions over SSH needs its own host port:
        # two VMs on the same forward silently break a parallel check-vms run.
        ports: dict[int, str] = {}
        for name, vm in cfg["vms"].items():
            port = (vm.get("ssh_provision") or {}).get("ssh_host_port")
            if port is None:
                continue
            self.assertNotIn(int(port), ports, f"{name} reuses port {port} of {ports.get(int(port))}")
            ports[int(port)] = name

        # The Ubuntu desktop flavors: one autoinstall recipe, the desktop metapackage and an
        # autologin drop-in for the display manager that flavor ships.
        for name, package, dm_file in (
            ("lubuntu-24.04", "lubuntu-desktop", "/etc/sddm.conf.d/vmctl-autologin.conf"),
            ("kubuntu-24.04", "kubuntu-desktop", "/etc/sddm.conf.d/vmctl-autologin.conf"),
            ("xubuntu-24.04", "xubuntu-desktop", "/etc/lightdm/lightdm.conf.d/vmctl-autologin.conf"),
            ("ubuntu-mate-24.04", "ubuntu-mate-desktop", "/etc/lightdm/lightdm.conf.d/vmctl-autologin.conf"),
            ("ubuntu-budgie-24.04", "ubuntu-budgie-desktop", "/etc/lightdm/lightdm.conf.d/vmctl-autologin.conf"),
        ):
            vm = cfg["vms"][name]
            self.assertIn(package, vm["autoinstall"]["packages"])
            self.assertEqual(vm["autoinstall"]["username"], "lab")
            self.assertEqual(vm["ssh_provision"]["user"], "lab")
            self.assertEqual(vm["cloud_init"]["user"], "lab")
            paths = [f["path"] for f in vm["cloud_init"]["write_files"]]
            self.assertIn(dm_file, paths)
            # ubuntu-budgie-desktop pulls gdm3 on 24.04 and ignored the lightdm drop-in, so
            # every flavor now carries the gdm3 autologin file as well (verified live).
            self.assertIn("/etc/gdm3/custom.conf", paths)

        # Fedora Silverblue rides the kickstart flow with an ostree source instead of %packages.
        silverblue = cfg["vms"]["fedora-silverblue"]
        self.assertIsNotNone(vmctl.kickstart.ostree_config(silverblue))
        self.assertEqual(silverblue["kickstart_config"]["ostree"]["ref_match"], "silverblue")
        self.assertNotIn("%packages", vmctl.kickstart.render_kickstart("fedora-silverblue", silverblue))

        # openSUSE Tumbleweed: AutoYaST profile, DVD loader kernel, SATA seed CD.
        tumbleweed = cfg["vms"]["opensuse-tumbleweed-autoyast"]
        self.assertEqual(tumbleweed["autoyast_config"]["username"], "lab")
        self.assertEqual(tumbleweed["installer_boot"]["kernel"], "boot/x86_64/loader/linux")
        self.assertIn("<pattern>gnome</pattern>", vmctl.autoyast.render_autoyast("opensuse-tumbleweed-autoyast", tumbleweed))

        # Windows 7: BIOS, e1000e (no NetKVM needed), no SSH (no OpenSSH on 7), generic identity.
        w7 = cfg["vms"]["windows7-unattended"]
        self.assertEqual(w7["firmware"]["type"], "bios")
        self.assertEqual(w7["network_device"], "e1000e")
        self.assertNotIn("ssh_provision", w7)
        self.assertNotIn("shared_dir", w7)
        self.assertTrue(vmctl.windows.is_legacy_windows(w7["windows_config"]))
        self.assertEqual(w7["windows_config"]["username"], "lab")
        self.assertFalse(w7["windows_config"]["bypass_requirements"])

        # The network lab: one topology on the router, members pointing at it, generic identities,
        # a local-only pfSense ISO and the Ubuntu 22.04.5 ISO with a public URL for the members.
        router = cfg["vms"]["pfsense-lab"]
        self.assertEqual(router["pfsense_config"]["username"], "lab")
        self.assertEqual(router["firmware"]["type"], "bios")
        self.assertNotIn("iso_url", router)
        top = vmctl.netlab.topology(cfg, "pfsense-lab")
        self.assertEqual(top["lan"]["name"], "lab-lan")
        self.assertEqual(top["dns_ip"], "192.168.0.10")
        self.assertEqual([m["name"] for m in top["members"]], ["lubuntu22-lab", "pihole-lab"])
        self.assertEqual(vmctl.netlab.lab_vm_names(cfg, "pfsense-lab"), ["pfsense-lab", "pihole-lab", "lubuntu22-lab"])
        for member in ("pihole-lab", "lubuntu22-lab"):
            vm = cfg["vms"][member]
            self.assertEqual(vm["autoinstall"]["username"], "lab")
            self.assertTrue(vm["iso_url"].startswith("https://releases.ubuntu.com/22.04.5/"))
            self.assertEqual([n["phase"] for n in vm["networks"]], ["install", "runtime"])
        self.assertEqual(cfg["vms"]["lubuntu22-lab"]["shared_dir"], {"source": "shared", "tag": "shared"})

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
