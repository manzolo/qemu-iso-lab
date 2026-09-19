import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.errors  # noqa: E402
import vmctl.lifecycle  # noqa: E402
import vmctl.nixos  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


HASH = "$6$labsalt0$POq.mGL6qhDmEnplwYiYiuKyYy.U8EuL0G.ROmcWjbMIHXpKeoKRB6MI2ObMDS3NHOQiB/R9E4pAiaNp5HKou/"

# What the NixOS installer ISO really ships, with the doubled slash of its own paths.
ISOLINUX = """\
SERIAL 0 115200
DEFAULT boot

LABEL boot
MENU LABEL NixOS 25.11.12484.b6018f87da91 Installer (Linux LTS)
LINUX /boot//nix/store/8a1f575-linux-6.12.93/bzImage
APPEND init=/nix/store/nhzqcxd-nixos-system-nixos-25.11.12484/init boot.shell_on_fail root=LABEL=nixos-minimal-25.11-x86_64 nohibernate loglevel=4 lsm=landlock,yama,bpf
INITRD /boot//nix/store/7qim4yq-initrd-linux-6.12.93/initrd

LABEL boot-latest_kernel
LINUX /boot//nix/store/other-linux-7.1.2/bzImage
APPEND init=/nix/store/v00kcwc-nixos-system-nixos-25.11.12484/init root=LABEL=nixos-minimal-25.11-x86_64
INITRD /boot//nix/store/2b6x08c-initrd-linux-7.1.2/initrd
"""


class NixosLiveBootTests(BaseVmctlTestCase):
    def test_boot_pieces_come_from_the_medium(self):
        """Kernel, initrd and init= are store paths that change with every ISO rebuild."""
        boot = vmctl.nixos.parse_live_boot(ISOLINUX)
        assert boot is not None
        self.assertEqual(boot["kernel"], "boot/nix/store/8a1f575-linux-6.12.93/bzImage")
        self.assertEqual(boot["initrd"], "boot/nix/store/7qim4yq-initrd-linux-6.12.93/initrd")
        self.assertEqual(boot["init"], "/nix/store/nhzqcxd-nixos-system-nixos-25.11.12484/init")
        self.assertEqual(boot["root_label"], "nixos-minimal-25.11-x86_64")
        append = vmctl.nixos.live_kernel_append(boot)
        self.assertIn("init=/nix/store/nhzqcxd-nixos-system-nixos-25.11.12484/init", append)
        self.assertIn("root=LABEL=nixos-minimal-25.11-x86_64", append)
        self.assertIn("console=ttyS0,115200", append)

    def test_unreadable_medium_reports_no_boot(self):
        self.assertIsNone(vmctl.nixos.parse_live_boot("nothing useful here"))
        self.assertIsNone(vmctl.nixos.resolve_live_boot(Path("/nonexistent.iso")))
        self.assertIsNone(vmctl.nixos.resolve_live_boot(Path("/nonexistent.iso"), dry_run=True))

    def test_live_prompt_is_what_the_stream_contains(self):
        """The literal "[nixos@nixos:~]$" never arrives: an escape sequence sits after the bracket."""
        stream = "\x1b[1;32m[\x1b]0;nixos@nixos: ~\x07nixos@nixos:~]$\x1b[0m "
        self.assertIn(vmctl.nixos.NIXOS_LIVE_PROMPT, stream)
        self.assertNotIn("[nixos@nixos:~]$", stream)


class NixosRenderTests(BaseVmctlTestCase):
    def _nixos_vm(self, **extra) -> None:
        self.vm_config["nixos_config"] = {
            "hostname": "nixos-test",
            "username": "tester",
            "password_hash": HASH,
            "timezone": "Europe/Rome",
            "locale": "en_US.UTF-8",
            "keymap": "it",
            "state_version": "25.11",
            **extra,
        }

    def test_configuration_defines_the_whole_guest(self):
        self._nixos_vm(packages=["git", "htop"])
        text = vmctl.nixos.render_configuration(self.vm_name, self.vm_config)
        self.assertIn("users.users.tester = {", text)
        self.assertIn(f'hashedPassword = "{HASH}";', text)
        self.assertIn("security.sudo.wheelNeedsPassword = false;", text)
        self.assertIn("services.openssh.enable = true;", text)
        self.assertIn("boot.loader.systemd-boot.enable = true;", text)
        self.assertIn('boot.kernelParams = [ "console=tty0" "console=ttyS0,115200" ];', text)
        self.assertIn("environment.systemPackages = with pkgs; [ git htop ];", text)
        self.assertIn('system.stateVersion = "25.11";', text)

    def test_state_version_is_required(self):
        self._nixos_vm()
        del self.vm_config["nixos_config"]["state_version"]
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.nixos.render_configuration(self.vm_name, self.vm_config)

    def test_desktop_is_a_field_not_a_code_path(self):
        self._nixos_vm(desktop="gnome")
        text = vmctl.nixos.render_configuration(self.vm_name, self.vm_config)
        self.assertIn("services.displayManager.gdm.enable = true;", text)
        self.assertIn("services.xserver.desktopManager.gnome.enable = true;", text)
        self.assertIn("services.displayManager.autoLogin = {", text)
        self.assertIn('user = "tester";', text)
        # Autologin and the tty1 getty fight over the console; NixOS documents this pair.
        self.assertIn('systemd.services."getty@tty1".enable = false;', text)
        self.vm_config["nixos_config"]["desktop"] = "plasma"
        text = vmctl.nixos.render_configuration(self.vm_name, self.vm_config)
        self.assertIn("services.displayManager.sddm.enable = true;", text)
        self.assertIn("services.desktopManager.plasma6.enable = true;", text)
        self.vm_config["nixos_config"]["desktop"] = "cinnamon"
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.nixos.render_configuration(self.vm_name, self.vm_config)

    def test_ssh_key_lands_in_the_configuration(self):
        self._nixos_vm()
        key_path = self.root / "id_ed25519.pub"
        key_path.write_text("ssh-ed25519 AAAATESTKEY lab@host\n", encoding="utf-8")
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2273}
        with mock.patch.object(vmctl.nixos.ssh, "resolve_ssh_public_key", return_value=key_path):
            text = vmctl.nixos.render_configuration(self.vm_name, self.vm_config)
        self.assertIn('openssh.authorizedKeys.keys = [ "ssh-ed25519 AAAATESTKEY lab@host" ];', text)

    def test_install_script_mounts_by_device_and_flushes_before_the_token(self):
        self._nixos_vm()
        script = vmctl.nixos.render_install_script(self.vm_name, self.vm_config)
        self.assertIn("nixos-generate-config --root", script)
        self.assertIn("nixos-install --no-root-passwd --root", script)
        # by-label lost the race against udev on the first live run; mount by device.
        self.assertNotIn("/dev/disk/by-label", script)
        self.assertIn('mount "$DISK"2 "$TARGET"', script)
        tail = script[script.index('log "Flushing"'):]
        self.assertLess(tail.index("blockdev --flushbufs"), tail.index(vmctl.nixos.BOOTSTRAP_COMPLETE_TOKEN))
        self.assertLess(tail.index(vmctl.nixos.BOOTSTRAP_COMPLETE_TOKEN), tail.index("poweroff -f"))
        self.assertIn(vmctl.nixos.BOOTSTRAP_FAILED_TOKEN, script)

    def test_identity_is_required(self):
        self.vm_config["nixos_config"] = {"username": "tester", "state_version": "25.11"}
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.nixos.render_install_script(self.vm_name, self.vm_config)

    def test_trigger_runs_the_seed_through_sudo(self):
        trigger = vmctl.nixos.live_trigger_command()
        self.assertIn("sudo mount -t iso9660 /dev/vdb", trigger)
        self.assertIn("sudo bash", trigger)

    def test_matrix_runs_it_through_bootstrap_nixos(self):
        self._nixos_vm()
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2273}
        mode, note = vmctl.lifecycle.local_test_mode(self.vm_config)
        self.assertEqual(mode, "bootstrap-nixos")
        self.assertIn("nixos-install", note)
        cfg = {"vms": {self.vm_name: self.vm_config}}
        self.assertEqual(vmctl.lifecycle.local_test_clean_candidates([self.vm_name], cfg), [self.vm_name])

    def test_check_profile_wants_efi_and_virtio(self):
        self._nixos_vm()
        self.vm_config["firmware"] = {"type": "efi"}
        self.vm_config["disk"] = dict(self.vm_config["disk"], interface="virtio")
        self.assertEqual(vmctl.nixos.check_profile(self.vm_name, self.vm_config), [])
        self.vm_config["firmware"] = {"type": "bios"}
        self.vm_config["disk"] = dict(self.vm_config["disk"], interface="ide")
        problems = vmctl.nixos.check_profile(self.vm_name, self.vm_config)
        self.assertTrue(any("efi" in p for p in problems), problems)
        self.assertTrue(any("virtio" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
