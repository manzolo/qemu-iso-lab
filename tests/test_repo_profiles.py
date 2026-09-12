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
import vmctl.preseed  # noqa: E402
import vmctl.reactos  # noqa: E402
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
            "alpine-ci-installed",
            "debian-efi",
            "debian-bios",
            "ubuntu-server-ci",
            "fedora-server-efi",
            "freebsd",
            "arch-omarchy-nvidia",
            "fedora-niri-dms",
            "alpine-niri",
            "cachyos-nvidia",
            "windows11-unattended",
            "windows10-unattended",
            "pfsense-lab",
            "pihole-lab",
            "lubuntu-lab",
            "windows7-unattended",
            "lubuntu-24.04",
            "kubuntu-24.04",
            "xubuntu-24.04",
            "ubuntu-mate-24.04",
            "ubuntu-budgie-24.04",
            "rocky-9",
            "fedora-silverblue",
            "opensuse-tumbleweed-autoyast",
            "ubuntu-8.04-desktop",
            "ubuntu-24.04-desktop",
            "reactos",
        ):
            self.assertIn(profile, cfg["vms"])

        # Live PASS dates recorded by the maintainer (docs/PROFILE_TODO.md): the whole unattended
        # matrix on 2026-09-09 (clean reinstall, TIMEOUT=3600), the Windows templates on 2026-09-06.
        verified_matrix = {
            "alpine-niri", "cachyos-desktop", "cachyos-nvidia", "arch-noctalia", "arch-dms", "arch-dms-nvidia",
            "arch-omarchy-nvidia", "debian-server", "ubuntu-niri", "fedora-niri-dms", "fedora-silverblue",
            "pfsense-lab", "pihole-lab", "lubuntu-lab", "opensuse-tumbleweed-autoyast", "almalinux-server",
            "rocky-9", "lubuntu-24.04", "kubuntu-24.04", "xubuntu-24.04", "ubuntu-mate-24.04",
            "ubuntu-budgie-24.04", "ubuntu-gnome-24.04", "windows7-unattended", "windows10-unattended",
            "windows11-unattended",
        }
        verified_templates = {"windows10-template", "windows11-template"}
        # The Ubuntu desktop history: clean reinstall of each with the final recipe on 2026-09-12.
        verified_history = {f"ubuntu-{v}-unattended" for v in ("8.04", "10.04", "12.04", "14.04", "16.04", "18.04", "20.04", "22.04")} | {"reactos"}
        for name, vm in cfg["vms"].items():
            self.assertIn(vm["meta"]["status"], ("manual", "unattended", "experimental"))
            expected_date = ("2026-09-09" if name in verified_matrix else "2026-09-06" if name in verified_templates
                             else "2026-09-12" if name in verified_history else None)
            self.assertEqual(vm["meta"].get("verified"), expected_date, name)
        # Promoted on 2026-09-09: verify-desktop reported an active local graphical session for
        # the autologin user on the live matrix, which is what the flavor recipe has to prove.
        self.assertEqual(cfg["vms"]["ubuntu-budgie-24.04"]["meta"]["status"], "unattended")

        # CI media must never drift when the upstream latest-stable pointer moves.
        for name in ("alpine-ci", "alpine-ci-installed"):
            vm = cfg["vms"][name]
            self.assertNotIn("iso_discovery", vm)
            self.assertNotIn("latest", vm["iso_url"])
            self.assertEqual(Path(vm["iso"]).name, vm["iso_url"].rsplit("/", 1)[1])
            self.assertRegex(vm["iso_sha256"], r"^[0-9a-f]{64}$")

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

        # Ubuntu desktop LTS history: one manual profile per LTS from 8.04 to 24.04, every ISO
        # pinned to the vendor SHA256SUMS (old-releases for the EOL ones, see docs/ISO_CHECKSUMS.md).
        lts = ("8.04", "10.04", "12.04", "14.04", "16.04", "18.04", "20.04", "22.04", "24.04")
        for version in lts:
            vm = cfg["vms"][f"ubuntu-{version}-desktop"]
            self.assertEqual(vm["meta"]["status"], "manual")
            self.assertRegex(vm["iso_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(Path(vm["iso"]).name, vm["iso_url"].rsplit("/", 1)[1])
            self.assertIn(f"ubuntu-{version}", vm["iso_url"])
            self.assertIn("old-releases.ubuntu.com" if float(version) < 14 else "releases.ubuntu.com", vm["iso_url"])
        # Kernel 2.6.24 has no virtio drivers: PATA disk and e1000 on a BIOS pc machine.
        hardy = cfg["vms"]["ubuntu-8.04-desktop"]
        self.assertEqual((hardy["disk"]["interface"], hardy["network_device"], hardy["firmware"]["type"], hardy["machine"]), ("ide", "e1000", "bios", "pc"))
        self.assertEqual(cfg["vms"]["ubuntu-10.04-desktop"]["disk"]["interface"], "virtio")
        # vmmouse-era guests: no VMware port, or the pointer never reaches the USB tablet (verified on 14.04).
        self.assertIs(cfg["vms"]["ubuntu-14.04-desktop"].get("vmport"), False)
        self.assertIs(cfg["vms"]["ubuntu-14.04-unattended"].get("vmport"), False)
        self.assertNotIn("vmport", cfg["vms"]["ubuntu-12.04-desktop"])

        # The unattended counterparts ride bootstrap-preseed on the d-i media (alternate CDs, the
        # 14.04 server ISO): desktop task, EOL mirror, and legacy SSH for the pre-6.5 sshd guests.
        for version, port, legacy in (("8.04", 2252, True), ("10.04", 2253, True), ("12.04", 2254, True), ("14.04", 2255, False),
                                      ("16.04", 2256, False), ("18.04", 2257, False)):
            vm = cfg["vms"][f"ubuntu-{version}-unattended"]
            self.assertEqual(vm["meta"]["status"], "unattended")
            self.assertEqual(vm["ssh_provision"]["ssh_host_port"], port)
            desktop = vm["preseed_config"]["tasks"] + vm["preseed_config"]["packages"]
            self.assertIn("ubuntu-desktop", desktop)
            # pgrep -x sees the 15-character comm, so the 8.04 session manager is "x-session-manag".
            self.assertEqual(vm["preseed_config"]["username"], "lab")
            self.assertEqual(vm["installer_boot"], {"kernel": "install/vmlinuz", "initrd": "install/initrd.gz"})
            self.assertEqual(vm["preseed_config"]["mirror_hostname"], "old-releases.ubuntu.com" if float(version) < 14 else "archive.ubuntu.com")
            self.assertEqual(vm["ssh_provision"].get("key_type"), "rsa" if legacy else None)
            self.assertEqual("ssh_options" in vm["ssh_provision"], legacy)
            self.assertIn("lab", vmctl.preseed.render_preseed(f"ubuntu-{version}-unattended", vm))
            # systemd guests (16.04+) run the shared verify-desktop; upstart ones a pgrep on the session.
            check = vm["ssh_provision"]["post_install_run"][0]
            self.assertIn("verify-desktop" if float(version) >= 16 else "x-session-manag(er)?", check)
        self.assertEqual(cfg["vms"]["ubuntu-8.04-unattended"]["preseed_config"]["disk_device"], "auto")
        # 20.04 and 22.04 ride the autoinstall recipe of ubuntu-gnome-24.04, which is the 24.04 entry.
        for version, port in (("20.04", 2258), ("22.04", 2259)):
            vm = cfg["vms"][f"ubuntu-{version}-unattended"]
            # 22.04 installs the metapackage as an autoinstall package; 20.04's subiquity 22.07 sees only
            # the CD pool at that point, so the desktop goes through a late-command (verified live).
            desktop_sources = vm["autoinstall"].get("packages", []) + vm["autoinstall"].get("late_commands", [])
            self.assertTrue(any("ubuntu-desktop" in entry for entry in desktop_sources), version)
            self.assertEqual(vm["ssh_provision"]["ssh_host_port"], port)
            self.assertEqual(vm["autoinstall"]["username"], "lab")
            self.assertIn("/etc/gdm3/custom.conf", [f["path"] for f in vm["cloud_init"]["write_files"]])
            self.assertIs(vm.get("vmport"), False)
            self.assertEqual(Path(vm["iso"]).name, vm["iso_url"].rsplit("/", 1)[1])
            self.assertEqual(vm["meta"]["status"], "unattended")
        self.assertEqual(cfg["vms"]["ubuntu-8.04-unattended"]["disk"]["interface"], "ide")

        # ReactOS: no virtio storage driver, no UEFI, no SMP in the release; user-supplied ISO
        # (SourceForge ships it zipped), sha256 is a repository pin.
        reactos = cfg["vms"]["reactos"]
        self.assertEqual((reactos["disk"]["interface"], reactos["network_device"], reactos["firmware"]["type"], reactos["cpus"]), ("ide", "e1000", "bios", 1))
        self.assertEqual((reactos["audio"], reactos["audio_device"]), (True, "ac97"))
        self.assertEqual(reactos["meta"]["status"], "unattended")
        self.assertEqual(reactos["reactos_config"]["password"], "lab")
        self.assertIn("UnattendSetupEnabled = yes", vmctl.reactos.render_unattend("reactos", reactos))
        self.assertNotIn("iso_url", reactos)

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
        self.assertEqual([m["name"] for m in top["members"]], ["lubuntu-lab", "pihole-lab"])
        self.assertEqual(vmctl.netlab.lab_vm_names(cfg, "pfsense-lab"), ["pfsense-lab", "pihole-lab", "lubuntu-lab"])
        for member in ("pihole-lab", "lubuntu-lab"):
            vm = cfg["vms"][member]
            self.assertEqual(vm["autoinstall"]["username"], "lab")
            self.assertTrue(vm["iso_url"].startswith("https://releases.ubuntu.com/22.04.5/"))
            self.assertEqual([n["phase"] for n in vm["networks"]], ["install", "runtime"])
        self.assertEqual(cfg["vms"]["lubuntu-lab"]["shared_dir"], {"source": "shared", "tag": "shared"})

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
        for profile in ("cachyos-desktop", "cachyos-nvidia"):
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
