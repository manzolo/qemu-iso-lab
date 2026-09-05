import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.alpine  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.errors  # noqa: E402
import vmctl.iso  # noqa: E402
import vmctl.lifecycle  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


HASH = "$6$labsalt0$POq.mGL6qhDmEnplwYiYiuKyYy.U8EuL0G.ROmcWjbMIHXpKeoKRB6MI2ObMDS3NHOQiB/R9E4pAiaNp5HKou/"


class AlpineRenderTests(BaseVmctlTestCase):
    def _alpine_vm(self, **extra) -> None:
        self.vm_config["alpine_config"] = {
            "hostname": "alpine-test",
            "username": "tester",
            "password_hash": HASH,
            "timezone": "Europe/Rome",
            "keyboard_layout": "it",
            "packages": ["niri", "foot"],
            "optional_packages": ["grim"],
            "chroot_commands": ["rc-update add greetd default"],
            **extra,
        }

    def test_answerfile_covers_every_setup_alpine_step(self):
        self._alpine_vm()
        answers = vmctl.alpine.render_answerfile(self.vm_name, self.vm_config)
        self.assertIn('KEYMAPOPTS="it it"', answers)
        self.assertIn('HOSTNAMEOPTS="alpine-test"', answers)
        self.assertIn("DEVDOPTS=udev", answers)
        self.assertIn("iface eth0 inet dhcp", answers)
        self.assertIn('TIMEZONEOPTS="Europe/Rome"', answers)
        self.assertIn('APKREPOSOPTS="-1 -c"', answers)
        self.assertIn('USEROPTS="-a -u -g audio,input,video,netdev tester"', answers)
        self.assertIn("SSHDOPTS=openssh", answers)
        self.assertIn('DISKOPTS="-m sys /dev/vda"', answers)
        self.assertNotIn("USERSSHKEY", answers)

    def test_answerfile_injects_configured_ssh_public_key(self):
        self._alpine_vm()
        key = self.root / "keys" / "id_ed25519"
        key.parent.mkdir(parents=True)
        key.write_text("private\n", encoding="utf-8")
        (self.root / "keys" / "id_ed25519.pub").write_text("ssh-ed25519 AAAATEST tester@host\n", encoding="utf-8")
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2234, "ssh_key": str(key)}
        answers = vmctl.alpine.render_answerfile(self.vm_name, self.vm_config)
        self.assertIn('USERSSHKEY="ssh-ed25519 AAAATEST tester@host"', answers)

    def test_install_script_runs_setup_alpine_then_chroot_steps(self):
        self._alpine_vm()
        script = vmctl.alpine.render_install_script(self.vm_name, self.vm_config)
        self.assertIn('export ERASE_DISKS="$DISK"', script)
        self.assertIn('setup-alpine -e -f "$SEED_DIR/answers"', script)
        self.assertIn("apk add sudo bash dbus dbus-openrc seatd seatd-openrc eudev niri foot", script)
        self.assertIn('for pkg in grim; do', script)
        self.assertIn(f"echo 'tester:{HASH}' | chpasswd -e", script)
        self.assertIn("echo '%wheel ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/wheel", script)
        self.assertIn("rc-update add greetd default", script)
        self.assertIn('chroot "$TARGET" /bin/sh /tmp/vmctl-chroot.sh', script)

    def test_install_script_flushes_before_token_and_powers_off(self):
        self._alpine_vm()
        script = vmctl.alpine.render_install_script(self.vm_name, self.vm_config)
        sync_at = script.rindex("\nsync\n")
        flush_at = script.index("blockdev --flushbufs")
        token_at = script.index(vmctl.alpine.BOOTSTRAP_COMPLETE_TOKEN)
        poweroff_at = script.index("poweroff -f")
        self.assertLess(sync_at, flush_at)
        self.assertLess(flush_at, token_at)
        self.assertLess(token_at, poweroff_at)
        self.assertTrue(script.rstrip().endswith("poweroff -f"))

    def test_render_requires_username_and_password_hash(self):
        self._alpine_vm(username="")
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.alpine.render_answerfile(self.vm_name, self.vm_config)
        self._alpine_vm(password_hash="")
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.alpine.render_install_script(self.vm_name, self.vm_config)

    def test_render_raises_without_alpine_config(self):
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.alpine.render_answerfile(self.vm_name, self.vm_config)


class AlpineBootstrapTests(BaseVmctlTestCase):
    def _alpine_vm(self) -> None:
        self.vm_config["alpine_config"] = {"hostname": "alpine-test", "username": "tester", "password_hash": HASH}

    def test_create_seed_iso_writes_files_and_calls_builder(self):
        self._alpine_vm()
        with mock.patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/xorriso" if name == "xorriso" else None), \
             mock.patch.object(vmctl.runtime, "run") as run_cmd:
            iso_path = vmctl.alpine.create_alpine_seed_iso(self.vm_name, self.vm_config)

        artifact_dir = self.root / "artifacts/testvm/alpine"
        self.assertEqual(iso_path, artifact_dir / "seed.iso")
        for name in ("answers", "install.sh", "run.sh"):
            self.assertTrue((artifact_dir / name).exists(), name)
        self.assertIn("sh /media/vmctl-seed/install.sh", (artifact_dir / "run.sh").read_text(encoding="utf-8"))
        cmd = run_cmd.call_args.args[0]
        self.assertIn("xorriso", cmd[0])
        self.assertIn("ALPINESEED", cmd)

    def test_seed_drive_args_use_virtio_cdrom(self):
        args = vmctl.alpine.seed_iso_drive_args(Path("/tmp/seed.iso"))
        self.assertEqual(args[0], "-drive")
        self.assertIn("if=virtio", args[1])
        self.assertIn("media=cdrom", args[1])

    def test_live_trigger_mounts_seed_and_runs_it(self):
        trigger = vmctl.alpine.live_trigger_command()
        self.assertIn("mount -t iso9660 /dev/vdb /media/vmctl-seed", trigger)
        self.assertTrue(trigger.endswith("sh /media/vmctl-seed/run.sh"))

    def test_boot_artifacts_follow_kernel_flavor(self):
        self._alpine_vm()
        with mock.patch.object(vmctl.iso, "extract_iso_member") as extract:
            kernel, initrd = vmctl.alpine.extract_alpine_boot_artifacts(self.vm_config, self.root / "isos/alpine.iso")
        members = [call.args[1] for call in extract.call_args_list]
        self.assertEqual(members, ["boot/vmlinuz-lts", "boot/initramfs-lts"])
        self.assertEqual(kernel, self.root / "artifacts/testvm/installer/vmlinuz")
        self.assertEqual(initrd, self.root / "artifacts/testvm/installer/initrd")

        self.vm_config["alpine_config"]["kernel_flavor"] = "virt"
        with mock.patch.object(vmctl.iso, "extract_iso_member") as extract:
            vmctl.alpine.extract_alpine_boot_artifacts(self.vm_config, self.root / "isos/alpine.iso")
        self.assertEqual([call.args[1] for call in extract.call_args_list], ["boot/vmlinuz-virt", "boot/initramfs-virt"])

    def test_live_kernel_append_has_virtio_and_serial(self):
        self.assertIn("virtio_blk", vmctl.alpine.LIVE_KERNEL_APPEND)
        self.assertIn("console=ttyS0,115200", vmctl.alpine.LIVE_KERNEL_APPEND)
        self.assertTrue(vmctl.alpine.LIVE_KERNEL_APPEND.startswith("modules=loop,squashfs,sd-mod,usb-storage"))


class AlpineCleanTests(BaseVmctlTestCase):
    def test_clean_removes_alpine_seed_dir(self):
        import argparse
        self.vm_config["alpine_config"] = {"hostname": "alpine-test", "username": "tester", "password_hash": HASH}
        self.write_config_dir()
        seed_dir = self.root / "artifacts/testvm/alpine"
        seed_dir.mkdir(parents=True)
        (seed_dir / "answers").write_text("x", encoding="utf-8")
        with mock.patch.object(vmctl.lifecycle, "cmd_stop", return_value=0):
            self.vmctl.cmd_clean(argparse.Namespace(vm=self.vm_name, all=False, dry_run=False, yes=True))
        self.assertFalse(seed_dir.exists())


if __name__ == "__main__":
    unittest.main()
