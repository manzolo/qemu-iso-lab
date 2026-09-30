"""Void Linux from its live ISO: profile checks, the install script, the live automation."""
import json
import unittest

from tests._common import ROOT
from vmctl import config, lifecycle, void
from vmctl.errors import VMError


class VoidTests(unittest.TestCase):
    def profile(self):
        return json.loads(json.dumps(config.load_tracked(ROOT / "vms" / "profiles")["void-xfce"]))

    def test_the_profile_is_the_live_iso_flow_on_efi_virtio(self):
        vm = self.profile()
        self.assertEqual(lifecycle.local_test_mode(vm)[0], "bootstrap-void")
        void.check_profile("void-xfce", vm)
        with self.assertRaises(VMError):
            void.check_profile("void-xfce", {**vm, "firmware": {"type": "bios"}})

    def test_the_script_partitions_installs_configures_and_ends_flush_token_poweroff(self):
        script = void.render_install_script("void-xfce", self.profile(), ["ssh-ed25519 KEY probe"])
        order = ["DISK=/dev/vda", "sfdisk --wipe always $DISK", "mkfs.ext4", "cp /var/db/xbps/keys/*", "xbps-install -Sy -R",
                 "chroot /mnt xbps-reconfigure -fa", "useradd -m -G wheel", "ssh-ed25519 KEY probe", "autologin-user=",
                 "grub-install --target=x86_64-efi", "umount -R /mnt", void.BOOTSTRAP_COMPLETE_TOKEN, "poweroff -f\n"]
        positions = [script.index(p) for p in order]
        self.assertEqual(positions, sorted(positions), order)
        self.assertIn(f"trap 'echo \"{void.BOOTSTRAP_FAILED_TOKEN}", script)
        self.assertIn("ln -sf /etc/sv/$s /mnt/etc/runit/runsvdir/default/", script)
        self.assertIn("console=ttyS0,115200", void.LIVE_KERNEL_APPEND)
        self.assertTrue(void.live_trigger_command().endswith("bash /vmctl-seed/install.sh"))
