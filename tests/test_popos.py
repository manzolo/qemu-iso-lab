"""Pop!_OS from its live ISO: distinst from the live shell, the chroot configuration, the checks."""
import json
import unittest

from tests._common import ROOT
from vmctl import config, lifecycle, popos
from vmctl.errors import VMError


class PopOSTests(unittest.TestCase):
    def profile(self):
        return json.loads(json.dumps(config.load_tracked(ROOT / "vms" / "profiles")["popos-cosmic"]))

    def test_the_tracked_profile_is_the_popos_flow_on_efi_virtio(self):
        vm = self.profile()
        self.assertEqual(lifecycle.local_test_mode(vm)[0], "bootstrap-popos")
        popos.check_profile("popos-cosmic", vm)
        for key, value in (("firmware", {"type": "bios"}), ("disk", {**vm["disk"], "interface": "ide"})):
            with self.subTest(key=key), self.assertRaises(VMError):
                popos.check_profile("popos-cosmic", {**vm, key: value})

    def test_the_casper_directory_comes_from_the_medium_grub_cfg(self):
        text = "linux /casper_pop-os_24.04_amd64_generic_debug_443/vmlinuz.efi boot=casper live-media-path=/casper_pop-os_24.04_amd64_generic_debug_443 hostname=pop-os"
        self.assertEqual(popos.parse_live_media_path(text), "casper_pop-os_24.04_amd64_generic_debug_443")
        self.assertIsNone(popos.parse_live_media_path("linux /casper/vmlinuz boot=casper"))
        append = popos.kernel_append("casper_x")
        self.assertIn("live-media-path=/casper_x", append)
        self.assertIn("systemd.unit=multi-user.target", append)
        self.assertIn("console=ttyS0,115200", append)

    def test_script_runs_distinst_then_configures_then_flush_token_poweroff(self):
        vm = self.profile()
        vm["popos_config"]["username"] = "probe"
        script = popos.render_install_script("popos-cosmic", vm, ["ssh-ed25519 KEY probe"], "casper_x")
        order = ["distinst -s /cdrom/casper_x/filesystem.squashfs", "usermod -p", "ssh-ed25519 KEY probe",
                 "[initial_session]", 'user = "probe"', "CosmicInitialSetup.desktop", "umount $T\n",
                 "blockdev --flushbufs", popos.BOOTSTRAP_COMPLETE_TOKEN, "\npoweroff -f"]
        positions = [script.index(piece) for piece in order]
        self.assertEqual(positions, sorted(positions), order)
        self.assertIn("probe ALL=(ALL) NOPASSWD: ALL", script)
        self.assertIn("--username probe", script)
        self.assertIn('--profile_icon "$ICON"', script)  # distinst requires it with --username


if __name__ == "__main__":
    unittest.main()
