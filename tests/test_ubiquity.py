"""bootstrap-ubiquity: the seed, the late script and the ISO graft for a Ubiquity live medium (Linux Mint)."""
import argparse
from pathlib import Path
from unittest import mock

import vmctl.iso
import vmctl.lifecycle
import vmctl.qemu
import vmctl.runtime
import vmctl.ubiquity as ubiquity
from tests._common import BaseVmctlTestCase
from vmctl.errors import VMError


class UbiquityRenderTests(BaseVmctlTestCase):
    def _mint_vm(self, **changes):
        self.vm_config["ubiquity_config"] = {"username": "tester", "password_hash": "$6$salt$hash", "hostname": "mint-test",
                                             "timezone": "Europe/Rome", "keyboard_layout": "it", "locale": "it_IT.UTF-8",
                                             "packages": ["openssh-server", "qemu-guest-agent"], "late_commands": ["touch /root/late"], **changes}
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2281}
        self.vm_config["disk"] = dict(self.vm_config["disk"], interface="virtio")
        return self.vm_config

    def test_the_seed_answers_every_page_and_ends_with_the_late_script(self):
        seed = ubiquity.render_seed(self.vm_name, self._mint_vm())
        for line in ("d-i debian-installer/locale string it_IT.UTF-8", "d-i localechooser/languagelist select it",
                     "d-i keyboard-configuration/layoutcode string it", "d-i netcfg/get_hostname string mint-test",
                     "d-i time/zone string Europe/Rome", "d-i partman-auto/disk string /dev/vda",
                     "d-i partman-auto/method string regular", "d-i partman/confirm_nooverwrite boolean true",
                     "d-i passwd/username string tester", "d-i passwd/user-password-crypted password $6$salt$hash",
                     "d-i grub-installer/bootdev string /dev/vda", "ubiquity ubiquity/summary note",
                     "ubiquity ubiquity/use_nonfree boolean false",
                     "ubiquity ubiquity/success_command string sh /cdrom/preseed/vmctl-late.sh"):
            self.assertIn(line + "\n", seed)
        self.assertNotIn("user-password password", seed)
        plain = ubiquity.render_seed(self.vm_name, self._mint_vm(password="s3cret", password_hash=None))
        self.assertIn("d-i passwd/user-password password s3cret\n", plain)
        self.assertIn("d-i passwd/user-password-again password s3cret\n", plain)

    def test_the_late_script_configures_the_target_then_unmounts_flushes_and_reports(self):
        (self.root / "artifacts" / self.vm_name / "ssh").mkdir(parents=True)
        with mock.patch.object(ubiquity.ssh, "resolve_ssh_public_key", return_value=None):
            script = ubiquity.render_late_script(self.vm_name, self._mint_vm())
        self.assertTrue(script.startswith("#!/bin/sh"))
        self.assertIn("exec >/dev/ttyS0 2>&1", script)
        self.assertIn("apt-get install -y -o Dpkg::Options::=--force-confnew openssh-server qemu-guest-agent", script)
        self.assertIn("printf '%s ALL=(ALL) NOPASSWD: ALL\\n' tester > /etc/sudoers.d/nopasswd-tester", script)
        self.assertIn("systemctl enable ssh.service serial-getty@ttyS0.service", script)
        self.assertIn("autologin-session=cinnamon", script)
        self.assertIn("bash -lc 'touch /root/late'", script)
        self.assertIn('chroot "$T" /bin/sh /tmp/vmctl-target.sh || fail', script)
        # The invariant: umount → sync → flush → token → poweroff, and the FAILED token on any failure.
        order = [script.rindex(part) for part in ('umount -R "$T"', "blockdev --flushbufs /dev/vda", ubiquity.BOOTSTRAP_COMPLETE_TOKEN, "poweroff -f")]
        self.assertEqual(order, sorted(order))
        self.assertLess(script.index("sync\numount -R"), script.index("blockdev --flushbufs"))
        self.assertIn(f'echo "{ubiquity.BOOTSTRAP_FAILED_TOKEN}: $*"', script)

    def test_the_project_key_lands_in_authorized_keys(self):
        key = self.root / "id.pub"
        key.write_text("ssh-ed25519 AAAA test\n")
        with mock.patch.object(ubiquity.ssh, "resolve_ssh_public_key", return_value=key):
            script = ubiquity.render_late_script(self.vm_name, self._mint_vm())
        self.assertIn("printf '%s\\n' 'ssh-ed25519 AAAA test' > /home/tester/.ssh/authorized_keys", script)
        self.assertIn("chmod 600 /home/tester/.ssh/authorized_keys", script)

    def test_kernel_append_boots_casper_in_automatic_mode_with_the_seed_from_the_medium(self):
        append = ubiquity.kernel_append(self._mint_vm())
        for part in ("boot=casper", "only-ubiquity", "automatic-ubiquity", "noprompt", "file=/cdrom/preseed/vmctl.seed",
                     "debian-installer/locale=it_IT.UTF-8", "keyboard-configuration/layoutcode=it", "console=ttyS0,115200n8"):
            self.assertIn(part, append)

    def test_check_profile_names_what_is_missing(self):
        self.assertEqual(ubiquity.check_profile(self.vm_name, self.vm_config), [f"{self.vm_name}: no ubiquity_config section"])
        vm = self._mint_vm(username="", password_hash=None)
        vm.pop("ssh_provision")
        vm["disk"] = dict(vm["disk"], interface="ide")
        problems = ubiquity.check_profile(self.vm_name, vm)
        self.assertTrue(any("username" in p for p in problems), problems)
        self.assertTrue(any("password_hash" in p for p in problems), problems)
        self.assertTrue(any("ssh_provision" in p for p in problems), problems)
        self.assertTrue(any("virtio" in p for p in problems), problems)
        self.assertEqual(ubiquity.check_profile(self.vm_name, self._mint_vm()), [])

    def test_the_install_iso_is_the_vendor_iso_plus_the_seed_directory_cached_by_stamp(self):
        vm = self._mint_vm()
        source = self.root / "isos" / "mint.iso"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"iso")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "cp":
                Path(cmd[-1]).write_bytes(b"copy")

        with mock.patch.object(ubiquity.ssh, "resolve_ssh_public_key", return_value=None), \
             mock.patch.object(vmctl.runtime, "run", side_effect=fake_run), \
             mock.patch.object(vmctl.runtime, "require_command"):
            dest = ubiquity.ensure_install_iso(self.vm_name, vm, source)
            self.assertEqual(dest, self.root / "artifacts" / self.vm_name / "ubiquity" / "install.iso")
            self.assertTrue(dest.is_file())
            xorriso = calls[1]
            self.assertEqual(xorriso[:5], ["xorriso", "-boot_image", "any", "keep", "-dev"])
            self.assertIn("/preseed/vmctl.seed", xorriso)
            self.assertIn("/preseed/vmctl-late.sh", xorriso)
            self.assertFalse((dest.parent / "iso-work").exists())
            # Same source and same rendering: nothing is rebuilt.
            self.assertEqual(ubiquity.ensure_install_iso(self.vm_name, vm, source), dest)
            self.assertEqual(len(calls), 2)
            # A different seed (new hostname) rebuilds.
            vm["ubiquity_config"]["hostname"] = "other"
            ubiquity.ensure_install_iso(self.vm_name, vm, source)
            self.assertEqual(len(calls), 4)

    def test_casper_boot_files_default_to_mints_initrd_lz(self):
        seen = []
        with mock.patch.object(vmctl.iso, "extract_iso_member", side_effect=lambda iso_path, member, dest, dry_run=False: seen.append(member)):
            ubiquity.extract_boot_artifacts(self._mint_vm(), self.root / "mint.iso")
        self.assertEqual(seen, ["casper/vmlinuz", "casper/initrd.lz"])

    def test_the_matrix_runs_it_through_bootstrap_ubiquity(self):
        vm = self._mint_vm()
        mode, note = vmctl.lifecycle.local_test_mode(vm)
        self.assertEqual(mode, "bootstrap-ubiquity")
        self.assertIn("Ubiquity", note)
        cfg = {"vms": {self.vm_name: vm}}
        self.assertEqual(vmctl.lifecycle.local_test_clean_candidates([self.vm_name], cfg), [self.vm_name])
        vm.pop("ssh_provision")
        self.assertEqual(vmctl.lifecycle.local_test_mode(vm)[0], "skip")

    def test_cmd_bootstrap_ubiquity_dry_run_boots_the_medium_then_the_installed_vm(self):
        self.vm_config["firmware"] = {"type": "efi", "code": "firmware/OVMF_CODE_4M.fd",
                                      "vars_template": "firmware/OVMF_VARS_4M.fd", "vars_path": "artifacts/testvm/OVMF_VARS.fd"}
        self._mint_vm()
        self.write_config_dir()
        args = argparse.Namespace(vm=self.vm_name, timeout=45, dry_run=True)
        with mock.patch.object(vmctl.iso, "ensure_iso", return_value=self.root / self.vm_config["iso"]), \
             mock.patch.object(vmctl.runtime, "require_command"), \
             mock.patch.object(vmctl.runtime, "run"), \
             mock.patch.object(ubiquity.ssh, "resolve_ssh_public_key", return_value=None), \
             mock.patch.object(vmctl.iso, "extract_iso_member"), \
             mock.patch.object(vmctl.qemu, "common_args", side_effect=[["qemu-system-x86_64"], ["qemu-system-x86_64"]]), \
             mock.patch.object(vmctl.qemu, "run_and_expect") as run_and_expect, \
             mock.patch.object(vmctl.lifecycle, "run_post_install") as run_post_install, \
             mock.patch.object(vmctl.runtime, "run_background", return_value=None):
            exit_code = self.vmctl.cmd_bootstrap_ubiquity(args)
        self.assertEqual(exit_code, 0)
        qemu_args = run_and_expect.call_args.args[0]
        self.assertIn("-cdrom", qemu_args)
        self.assertTrue(str(qemu_args[qemu_args.index("-cdrom") + 1]).endswith("ubiquity/install.iso"))
        self.assertIn("automatic-ubiquity", qemu_args[qemu_args.index("-append") + 1])
        self.assertEqual(run_and_expect.call_args.kwargs["expected_text"], ubiquity.BOOTSTRAP_COMPLETE_TOKEN)
        run_post_install.assert_called_once_with(self.vm_name, self.vm_config, 45, dry_run=True)

    def test_a_profile_without_ubiquity_config_is_refused(self):
        self.write_config_dir()
        with self.assertRaisesRegex(VMError, "no ubiquity_config section"):
            self.vmctl.cmd_bootstrap_ubiquity(argparse.Namespace(vm=self.vm_name, timeout=45, dry_run=True))
