import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.archinstall  # noqa: E402
import vmctl.errors  # noqa: E402
import vmctl.pearos  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


HASH = "$6$labsalt0$POq.mGL6qhDmEnplwYiYiuKyYy.U8EuL0G.ROmcWjbMIHXpKeoKRB6MI2ObMDS3NHOQiB/R9E4pAiaNp5HKou/"


class PearosRenderTests(BaseVmctlTestCase):
    def _pearos_vm(self, **extra) -> None:
        self.vm_config["pearos_config"] = {
            "hostname": "pearos-test",
            "username": "tester",
            "password_hash": HASH,
            "timezone": "Europe/Rome",
            "locale": "en_US.UTF-8",
            "keymap": "it",
            **extra,
        }

    def test_script_reproduces_the_unpackfs_install(self):
        self._pearos_vm()
        script = vmctl.pearos.render_install_script(self.vm_name, self.vm_config)
        # The two entries of their unpackfs.conf, and nothing that pacstraps a package list.
        self.assertIn("unsquashfs -f -d \"$TARGET\" \"$SFS\"", script)
        self.assertIn("/run/archiso/bootmnt/arch/x86_64/airootfs.sfs", script)
        self.assertIn("$TARGET/boot/vmlinuz-linux-cachyos-lts", script)
        self.assertNotIn("pacstrap", script)
        self.assertIn("grub-install --target=x86_64-efi", script)
        self.assertIn("useradd -m -g users -G wheel,audio,video,storage,network,power,lp -s /bin/bash tester", script)
        self.assertIn("'tester:" + HASH + "'", script)
        self.assertIn("chpasswd -e", script)
        self.assertIn("ln -sf /usr/share/zoneinfo/Europe/Rome", script)
        self.assertIn("KEYMAP=it", script)
        self.assertIn("User=%s\\nSession=%s", script)

    def test_live_packages_go_before_the_initramfs_is_rebuilt(self):
        """Removing mkinitcpio-archiso rebuilds the initramfs; the archiso hooks must be gone first.

        Doing it the other way round overwrote a good image with one built after
        'Hook archiso cannot be found' (verified live on 2026-09-19).
        """
        self._pearos_vm()
        script = vmctl.pearos.render_install_script(self.vm_name, self.vm_config)
        removal = script.index("pacman -R --noconfirm")
        strip_hooks = script.index("s/\\s*archiso[a-z_]*//g")
        build = script.index("mkinitcpio -P")
        self.assertLess(strip_hooks, removal)
        self.assertLess(removal, build)

    def test_completion_token_follows_the_flush(self):
        self._pearos_vm()
        script = vmctl.pearos.render_install_script(self.vm_name, self.vm_config)
        tail = script[script.index("log \"Flushing\""):]
        self.assertLess(tail.index("blockdev --flushbufs"), tail.index(vmctl.pearos.BOOTSTRAP_COMPLETE_TOKEN))
        self.assertLess(tail.index(vmctl.pearos.BOOTSTRAP_COMPLETE_TOKEN), tail.index("poweroff -f"))
        self.assertIn(vmctl.pearos.BOOTSTRAP_FAILED_TOKEN, script)

    def test_first_boot_wizard_is_removed_because_the_script_did_its_job(self):
        self._pearos_vm()
        script = vmctl.pearos.render_install_script(self.vm_name, self.vm_config)
        self.assertIn("xyz.pearos-post-install.desktop", script)
        self.assertIn("userdel -rf liveuser", script)

    def test_ssh_public_key_is_installed_when_the_profile_provisions(self):
        self._pearos_vm()
        key_path = self.root / "id_ed25519.pub"
        key_path.write_text("ssh-ed25519 AAAATESTKEY lab@host\n", encoding="utf-8")
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2272}
        with mock.patch.object(vmctl.pearos.ssh, "resolve_ssh_public_key", return_value=key_path):
            script = vmctl.pearos.render_install_script(self.vm_name, self.vm_config)
        self.assertIn("ssh-ed25519 AAAATESTKEY lab@host", script)
        self.assertIn("/home/tester/.ssh/authorized_keys", script)

    def test_identity_is_required(self):
        self.vm_config["pearos_config"] = {"username": "tester"}
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.pearos.render_install_script(self.vm_name, self.vm_config)
        self.vm_config["pearos_config"] = {"password_hash": HASH}
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.pearos.render_install_script(self.vm_name, self.vm_config)

    def test_live_boot_uses_the_medium_label_and_stays_off_the_graphical_target(self):
        with mock.patch.object(vmctl.archinstall, "arch_iso_label", return_value="pearOS_NiceC0re_202609"):
            label = vmctl.pearos.pearos_iso_label(Path("/nonexistent.iso"))
        self.assertEqual(label, "pearOS_NiceC0re_202609")
        append = vmctl.pearos.live_kernel_append(label)
        self.assertIn("archisolabel=pearOS_NiceC0re_202609", append)
        self.assertIn("console=ttyS0,115200", append)
        self.assertIn("systemd.unit=multi-user.target", append)
        # An Arch fallback label would boot nothing here: the medium is pearOS.
        with mock.patch.object(vmctl.archinstall, "arch_iso_label", return_value="ARCH_LIVE"):
            self.assertEqual(vmctl.pearos.pearos_iso_label(Path("/nonexistent.iso")), "pearOS_NiceC0re")

    def test_trigger_mounts_the_seed_at_the_live_prompt(self):
        self.assertIn("mount -t iso9660 /dev/vdb", vmctl.pearos.live_trigger_command())
        self.assertIn("install.sh", vmctl.pearos.live_trigger_command())
        self.assertEqual(vmctl.pearos.PEAROS_SERIAL_LOGIN_PROMPT, "pearOS-Live-System login:")

    def test_check_profile_rejects_bios_and_non_virtio_disks(self):
        self._pearos_vm()
        self.vm_config["firmware"] = {"type": "bios"}
        self.vm_config["disk"] = dict(self.vm_config["disk"], interface="ide")
        problems = vmctl.pearos.check_profile(self.vm_name, self.vm_config)
        self.assertTrue(any("efi" in p for p in problems), problems)
        self.assertTrue(any("virtio" in p for p in problems), problems)

    def test_check_profile_accepts_the_tracked_shape(self):
        self._pearos_vm()
        self.vm_config["firmware"] = {"type": "efi"}
        self.vm_config["disk"] = dict(self.vm_config["disk"], interface="virtio")
        self.assertEqual(vmctl.pearos.check_profile(self.vm_name, self.vm_config), [])


if __name__ == "__main__":
    unittest.main()
