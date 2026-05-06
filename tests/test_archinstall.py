import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.archinstall  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.ssh  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class ArchinstallConfigTests(BaseVmctlTestCase):
    def _arch_vm(self) -> None:
        self.vm_config["archinstall_config"] = {
            "hostname": "arch-test",
            "username": "tester",
            "password": "s3cret",
            "timezone": "Europe/Rome",
            "keyboard_layout": "it",
            "locale_lang": "it_IT",
            "locale_enc": "UTF-8",
            "bootloader": "Grub",
            "kernels": ["linux"],
            "audio": True,
            "packages": ["pipewire"],
            "services": ["sshd"],
        }

    def test_render_archinstall_config_hostname_and_locale(self):
        self._arch_vm()
        rendered = vmctl.archinstall.render_archinstall_config(self.vm_name, self.vm_config)
        payload = json.loads(rendered)
        self.assertEqual(payload["hostname"], "arch-test")
        self.assertEqual(payload["timezone"], "Europe/Rome")
        self.assertEqual(payload["locale_config"]["kb_layout"], "it")
        self.assertEqual(payload["locale_config"]["sys_lang"], "it_IT")

    def test_render_archinstall_config_includes_base_packages(self):
        self._arch_vm()
        rendered = vmctl.archinstall.render_archinstall_config(self.vm_name, self.vm_config)
        payload = json.loads(rendered)
        for pkg in ("base-devel", "git", "openssh", "networkmanager", "pipewire"):
            self.assertIn(pkg, payload["packages"])

    def test_render_archinstall_config_injects_nopasswd_custom_command(self):
        self._arch_vm()
        rendered = vmctl.archinstall.render_archinstall_config(self.vm_name, self.vm_config)
        payload = json.loads(rendered)
        cmds = payload["custom-commands"]
        self.assertTrue(any("NOPASSWD" in c for c in cmds))

    def test_render_archinstall_config_audio_pipewire(self):
        self._arch_vm()
        rendered = vmctl.archinstall.render_archinstall_config(self.vm_name, self.vm_config)
        payload = json.loads(rendered)
        self.assertEqual(payload.get("audio_config"), {"audio": "pipewire"})

    def test_render_archinstall_config_no_audio(self):
        self._arch_vm()
        self.vm_config["archinstall_config"]["audio"] = False
        rendered = vmctl.archinstall.render_archinstall_config(self.vm_name, self.vm_config)
        payload = json.loads(rendered)
        self.assertNotIn("audio_config", payload)

    def test_render_archinstall_config_raises_without_section(self):
        with self.assertRaises(vmctl.archinstall.VMError):
            vmctl.archinstall.render_archinstall_config(self.vm_name, self.vm_config)

    def test_render_archinstall_creds_username_and_groups(self):
        self._arch_vm()
        rendered = vmctl.archinstall.render_archinstall_creds(self.vm_config)
        creds = json.loads(rendered)
        users = creds["!users"]
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["username"], "tester")
        self.assertEqual(users[0]["!password"], "s3cret")
        self.assertIn("wheel", users[0]["groups"])
        self.assertTrue(users[0]["sudo"])

    def test_render_archinstall_creds_raises_without_username(self):
        self._arch_vm()
        del self.vm_config["archinstall_config"]["username"]
        with self.assertRaises(vmctl.archinstall.VMError):
            vmctl.archinstall.render_archinstall_creds(self.vm_config)

    def test_render_archinstall_creds_raises_without_password(self):
        self._arch_vm()
        del self.vm_config["archinstall_config"]["password"]
        with self.assertRaises(vmctl.archinstall.VMError):
            vmctl.archinstall.render_archinstall_creds(self.vm_config)

    def test_create_config_iso_writes_files_and_calls_builder(self):
        self._arch_vm()
        with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/xorriso" if name == "xorriso" else None), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            iso_path = vmctl.archinstall.create_config_iso(self.vm_name, self.vm_config)

        artifact_dir = self.root / "artifacts/testvm/archinstall"
        self.assertTrue((artifact_dir / "archinstall-config.json").exists())
        self.assertTrue((artifact_dir / "archinstall-creds.json").exists())
        self.assertTrue((artifact_dir / "run.sh").exists())
        self.assertEqual(iso_path, artifact_dir / "archinstall-config.iso")

        cmd = run_cmd.call_args.args[0]
        self.assertIn("xorriso", cmd[0])
        self.assertIn("ARCHCONF", cmd)
        self.assertIn(str(artifact_dir / "archinstall-config.iso"), cmd)

    def test_config_iso_drive_args_uses_virtio_cdrom(self):
        iso_path = Path("/tmp/archinstall-config.iso")
        args = vmctl.archinstall.config_iso_drive_args(iso_path)
        self.assertEqual(len(args), 2)
        self.assertEqual(args[0], "-drive")
        self.assertIn("if=virtio", args[1])
        self.assertIn("media=cdrom", args[1])
        self.assertIn(str(iso_path), args[1])


class ArchinstallBootstrapTests(BaseVmctlTestCase):
    def _arch_vm(self) -> None:
        self.vm_config["archinstall_config"] = {
            "hostname": "arch-test",
            "username": "tester",
            "password": "s3cret",
            "timezone": "Europe/Rome",
            "keyboard_layout": "it",
            "locale_lang": "it_IT",
            "locale_enc": "UTF-8",
            "packages": ["pipewire"],
        }

    def test_render_bootstrap_script_contains_hostname_and_user(self):
        self._arch_vm()
        script = vmctl.archinstall.render_bootstrap_script(self.vm_name, self.vm_config)
        self.assertIn("arch-test", script)
        self.assertIn("tester", script)
        self.assertIn("Europe/Rome", script)
        self.assertIn("it_IT", script)

    def test_render_bootstrap_script_contains_sgdisk_and_pacstrap(self):
        self._arch_vm()
        script = vmctl.archinstall.render_bootstrap_script(self.vm_name, self.vm_config)
        self.assertIn("sgdisk", script)
        self.assertIn("pacstrap", script)
        self.assertIn("grub-install", script)
        self.assertIn("grub-mkconfig", script)
        self.assertIn("efibootmgr", script)
        self.assertIn("/boot/efi/EFI/BOOT/BOOTX64.EFI", script)
        self.assertIn("/boot/efi/EFI/GRUB/grubx64.efi", script)
        self.assertIn("rm -f /mnt/boot/grub/grub.cfg", script)
        self.assertIn("arch-chroot /mnt grub-mkconfig -o /boot/grub/grub.cfg", script)
        self.assertIn("/mnt/boot/grub/grub.cfg.new.new.new", script)
        self.assertIn("install -D -m 600 \"$candidate\" /mnt/boot/grub/grub.cfg", script)
        self.assertIn("arch-chroot /mnt grub-script-check /boot/grub/grub.cfg", script)
        self.assertIn("ROOT_UUID=\"$(blkid -s UUID -o value /dev/vda2)\"", script)
        self.assertIn("arch-chroot /mnt grub-mkstandalone", script)
        self.assertIn("--format=x86_64-efi", script)
        self.assertIn("--output=/boot/efi/EFI/GRUB/grubx64.efi", script)
        self.assertIn("part_gpt", script)
        self.assertIn("ext2", script)
        self.assertIn("boot/grub/grub.cfg=/grub-embedded.cfg", script)
        self.assertIn("/mnt/boot/efi/EFI/GRUB/grub.cfg", script)
        self.assertIn("search --no-floppy --fs-uuid --set=root $ROOT_UUID", script)
        self.assertIn("configfile \\$prefix/grub.cfg", script)
        self.assertIn("/mnt/boot/efi/EFI/BOOT/grub.cfg", script)
        self.assertIn("console=ttyS0,115200", script)
        self.assertIn("sync", script)
        self.assertIn("blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true", script)
        self.assertNotIn("grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=GRUB --removable", script)

    def test_render_bootstrap_script_installs_requested_kernel_and_grub_packages(self):
        self._arch_vm()
        self.vm_config["archinstall_config"]["kernels"] = ["linux", "linux-zen"]
        script = vmctl.archinstall.render_bootstrap_script(self.vm_name, self.vm_config)
        self.assertIn("linux-zen", script)
        self.assertIn("grub", script)
        self.assertIn("efibootmgr", script)

    def test_render_bootstrap_script_runs_bootstrap_chroot_commands(self):
        self._arch_vm()
        self.vm_config["archinstall_config"]["bootstrap_chroot_commands"] = [
            "pacman -S --noconfirm --needed git",
            "systemctl enable greetd",
        ]
        script = vmctl.archinstall.render_bootstrap_script(self.vm_name, self.vm_config)
        self.assertIn("Bootstrap guest customization", script)
        self.assertIn("arch-chroot /mnt bash -lc 'pacman -S --noconfirm --needed git'", script)
        self.assertIn("arch-chroot /mnt bash -lc 'systemctl enable greetd'", script)

    def test_render_bootstrap_script_generates_ssh_key_for_ssh_provision(self):
        self._arch_vm()
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2223}
        key_path = self.root / "artifacts/testvm/ssh/id_ed25519"
        pub_path = self.root / "artifacts/testvm/ssh/id_ed25519.pub"
        pub_path.parent.mkdir(parents=True)
        pub_path.write_text("ssh-ed25519 AAAATEST generated\n", encoding="utf-8")

        with mock.patch.object(vmctl.ssh, "ensure_generated_ssh_keypair", return_value=key_path):
            script = vmctl.archinstall.render_bootstrap_script(self.vm_name, self.vm_config)

        self.assertIn("Installing SSH public key for tester", script)
        self.assertIn("ssh-ed25519 AAAATEST generated", script)

    def test_render_bootstrap_script_ends_with_complete_token_and_poweroff(self):
        self._arch_vm()
        script = vmctl.archinstall.render_bootstrap_script(self.vm_name, self.vm_config)
        self.assertIn(vmctl.archinstall.BOOTSTRAP_COMPLETE_TOKEN, script)
        self.assertIn("poweroff", script)

    def test_serial_login_prompt_constant_is_non_empty(self):
        self.assertTrue(vmctl.archinstall.ARCH_SERIAL_LOGIN_PROMPT)

    def test_render_bootstrap_script_raises_without_username(self):
        self._arch_vm()
        del self.vm_config["archinstall_config"]["username"]
        with self.assertRaises(vmctl.archinstall.VMError):
            vmctl.archinstall.render_bootstrap_script(self.vm_name, self.vm_config)

    def test_arch_iso_label_parses_filename(self):
        cases = [
            ("archlinux-2026.04.01-x86_64.iso", "ARCH_202604"),
            ("archlinux-2024.12.01-x86_64.iso", "ARCH_202412"),
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename):
                label = vmctl.archinstall.arch_iso_label(Path(filename))
                self.assertEqual(label, expected)

    def test_arch_iso_label_fallback(self):
        label = vmctl.archinstall.arch_iso_label(Path("unknown.iso"))
        self.assertEqual(label, "ARCH_LIVE")

    def test_create_bootstrap_iso_writes_install_sh_and_calls_builder(self):
        self._arch_vm()
        with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/xorriso" if name == "xorriso" else None), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            iso_path = vmctl.archinstall.create_bootstrap_iso(self.vm_name, self.vm_config)

        artifact_dir = self.root / "artifacts/testvm/archinstall"
        self.assertTrue((artifact_dir / "install.sh").exists())
        self.assertTrue((artifact_dir / "run.sh").exists())
        self.assertEqual(iso_path, artifact_dir / "bootstrap.iso")

        cmd = run_cmd.call_args.args[0]
        self.assertIn("ARCHBOOT", cmd)


if __name__ == "__main__":
    unittest.main()
