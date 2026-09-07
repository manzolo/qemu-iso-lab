import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.autoyast  # noqa: E402
import vmctl.errors  # noqa: E402
import vmctl.runtime  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class AutoyastRenderTests(BaseVmctlTestCase):
    def _vm(self) -> dict:
        self.vm_config["autoyast_config"] = {
            "hostname": "tumbleweed",
            "username": "tester",
            "realname": "Test User",
            "password_hash": "$6$salt$hash",
            "timezone": "Europe/Rome",
            "keyboard_layout": "italian",
            "language": "it_IT",
            "disk_device": "vda",
            "patterns": ["enhanced_base", "gnome"],
            "packages": ["openssh", "vim"],
            "enable_services": ["NetworkManager", "sshd"],
            "chroot_commands": ["echo custom-step"],
        }
        return self.vm_config

    def test_profile_carries_partitioning_software_and_chroot_script(self):
        vm = self._vm()
        xml = vmctl.autoyast.render_autoyast("tumbleweed", vm)
        self.assertIn('<?xml version="1.0"?>', xml)
        self.assertIn("<timezone>Europe/Rome</timezone>", xml)
        self.assertIn("<keymap>italian</keymap>", xml)
        self.assertIn("<device>/dev/vda</device>", xml)
        self.assertIn("<loader_type>grub2-efi</loader_type>", xml)
        for pattern in ("enhanced_base", "gnome"):
            self.assertIn(f"<pattern>{pattern}</pattern>", xml)
        self.assertIn("<package>openssh</package>", xml)
        self.assertIn("<service>sshd</service>", xml)
        # the installer must not ask anything and must not reboot into a second stage
        self.assertIn("<confirm config:type=\"boolean\">false</confirm>", xml)
        self.assertIn("<service>YaST2-Second-Stage</service>", xml)
        self.assertIn("echo custom-step", xml)

    def test_chroot_script_flushes_before_the_completion_token(self):
        vm = self._vm()
        script = vmctl.autoyast.chroot_script("tumbleweed", vm)
        sync_at = script.index("\nsync\n")
        flush_at = script.index("blockdev --flushbufs")
        token_at = script.index(vmctl.autoyast.BOOTSTRAP_COMPLETE_TOKEN)
        # The project invariant: sync, flush, only then the token the host waits for.
        self.assertLess(sync_at, flush_at)
        self.assertLess(flush_at, token_at)
        self.assertIn("usermod -p '$6$salt$hash' tester", script)
        self.assertIn("/etc/sudoers.d/tester", script)
        self.assertIn("systemctl enable sshd.service", script)
        # openSUSE's firewalld opens dhcpv6-client only: without this the forwarded SSH port
        # accepts the connection and then hangs at the banner (verified live).
        self.assertIn("firewall-offline-cmd --add-service=ssh", script)
        firewall_at = script.index("firewall-offline-cmd")
        self.assertLess(firewall_at, sync_at)

    def test_missing_identity_is_rejected(self):
        self.vm_config["autoyast_config"] = {"username": "tester"}
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.autoyast.render_autoyast("tumbleweed", self.vm_config)
        self.vm_config["autoyast_config"] = {"password_hash": "$6$x$y"}
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.autoyast.render_autoyast("tumbleweed", self.vm_config)

    def test_kernel_append_points_linuxrc_at_the_usb_seed(self):
        vm = self._vm()
        append = vmctl.autoyast.kernel_append(vm)
        self.assertIn("autoyast=usb:///autoinst.xml", append)
        self.assertIn("console=ttyS0,115200", append)
        self.assertNotIn("install=", append)
        vm["autoyast_config"]["install_repo"] = "https://example.invalid/repo/oss/"
        self.assertTrue(vmctl.autoyast.kernel_append(vm).startswith("install=https://example.invalid/repo/oss/"))
        vm["autoyast_config"]["kernel_append"] = "vga=off"
        self.assertTrue(vmctl.autoyast.kernel_append(vm).endswith("vga=off"))

    def test_only_the_install_medium_is_a_cdrom_and_the_seed_is_usb(self):
        args = vmctl.autoyast.install_media_args(Path("/tmp/install.iso"), Path("/tmp/seed.iso"))
        joined = " ".join(args)
        # linuxrc only scans /dev/sr* for the repository, so the install ISO must be a real CD;
        # a second CD makes YaST ask which drive holds Disc 1 and the install stops (seen live).
        self.assertIn("ide-cd,drive=aycd0,bus=ide.0", joined)
        self.assertIn("usb-storage,drive=ayseed", joined)
        self.assertNotIn("aycd1", joined)
        self.assertNotIn("if=virtio", joined)

    def test_boot_artifacts_come_from_the_dvd_loader_directory(self):
        vm = self._vm()
        with mock.patch.object(vmctl.autoyast.iso, "extract_iso_member") as extract:
            kernel, initrd = vmctl.autoyast.extract_autoyast_boot_artifacts(vm, Path("/tmp/tw.iso"))
        members = [call.args[1] for call in extract.call_args_list]
        self.assertEqual(members, ["boot/x86_64/loader/linux", "boot/x86_64/loader/initrd"])
        self.assertEqual(kernel.name, "linux")
        self.assertEqual(initrd.name, "initrd")


if __name__ == "__main__":
    unittest.main()
