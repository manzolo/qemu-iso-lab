import gzip
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.preseed  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.ssh  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class PreseedConfigTests(BaseVmctlTestCase):
    def _preseed_vm(self) -> None:
        self.vm_config["preseed_config"] = {
            "hostname": "debian-test",
            "domain": "local",
            "username": "tester",
            "password": "s3cret",
            "timezone": "Europe/Rome",
            "keyboard_layout": "it",
            "locale": "it_IT.UTF-8",
            "language": "it",
            "country": "IT",
            "mirror_hostname": "deb.debian.org",
            "mirror_directory": "/debian",
            "tasks": ["standard"],
            "packages": ["openssh-server", "sudo", "curl", "ca-certificates"],
            "late_commands": [],
            "disk_device": "/dev/vda"
        }

    def test_render_preseed_hostname_and_locale(self):
        self._preseed_vm()
        rendered = vmctl.preseed.render_preseed(self.vm_name, self.vm_config)
        self.assertIn("d-i netcfg/get_hostname string debian-test", rendered)
        self.assertIn("d-i time/zone string Europe/Rome", rendered)
        self.assertIn("d-i debian-installer/locale string it_IT.UTF-8", rendered)
        self.assertIn("d-i debian-installer/language string it", rendered)
        self.assertIn("d-i debian-installer/country string IT", rendered)
        self.assertIn("d-i keyboard-configuration/xkb-keymap select it", rendered)

    def test_render_preseed_includes_packages(self):
        self._preseed_vm()
        rendered = vmctl.preseed.render_preseed(self.vm_name, self.vm_config)
        self.assertIn("d-i pkgsel/include string openssh-server sudo curl ca-certificates", rendered)

    def test_render_preseed_raises_without_username(self):
        self._preseed_vm()
        del self.vm_config["preseed_config"]["username"]
        with self.assertRaises(vmctl.preseed.VMError):
            vmctl.preseed.render_preseed(self.vm_name, self.vm_config)

    def test_render_preseed_raises_without_password(self):
        self._preseed_vm()
        del self.vm_config["preseed_config"]["password"]
        with self.assertRaises(vmctl.preseed.VMError):
            vmctl.preseed.render_preseed(self.vm_name, self.vm_config)

    def test_render_late_command_script_sudoers(self):
        self._preseed_vm()
        rendered = vmctl.preseed.render_late_command_script(self.vm_name, self.vm_config)
        self.assertIn("echo 'tester ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/nopasswd-tester", rendered)
        self.assertIn("usermod -aG sudo tester", rendered)

    def test_render_late_command_script_custom_commands(self):
        self._preseed_vm()
        self.vm_config["preseed_config"]["late_commands"] = ["touch /tmp/custom"]
        rendered = vmctl.preseed.render_late_command_script(self.vm_name, self.vm_config)
        self.assertIn("bash -lc 'touch /tmp/custom'", rendered)

    def test_render_late_command_script_generates_ssh_key_for_ssh_provision(self):
        self._preseed_vm()
        self.vm_config["ssh_provision"] = {"user": "tester", "ssh_host_port": 2223}
        key_path = self.root / "artifacts/testvm/ssh/id_ed25519"
        pub_path = self.root / "artifacts/testvm/ssh/id_ed25519.pub"
        pub_path.parent.mkdir(parents=True)
        pub_path.write_text("ssh-ed25519 AAAATEST generated\n", encoding="utf-8")

        with mock.patch.object(vmctl.ssh, "ensure_generated_ssh_keypair", return_value=key_path):
            script = vmctl.preseed.render_late_command_script(self.vm_name, self.vm_config)

        self.assertIn("Installing SSH public key for tester", script)
        self.assertIn("ssh-ed25519 AAAATEST generated", script)
        self.assertIn("/home/tester/.ssh/authorized_keys", script)
        self.assertNotIn("/target/home/tester/.ssh", script)

    def test_render_preseed_plaintext_password_emits_two_directives_on_separate_lines(self):
        self._preseed_vm()
        rendered = vmctl.preseed.render_preseed(self.vm_name, self.vm_config)
        self.assertIn("d-i passwd/user-password password s3cret", rendered)
        self.assertIn("d-i passwd/user-password-again password s3cret", rendered)
        self.assertNotIn("\\n", rendered)

    def test_render_late_command_script_ends_with_complete_token(self):
        self._preseed_vm()
        script = vmctl.preseed.render_late_command_script(self.vm_name, self.vm_config)
        self.assertIn("sync", script)
        self.assertIn("blockdev --flushbufs /dev/vda /dev/vda1 /dev/vda2 || true", script)
        self.assertIn(vmctl.preseed.BOOTSTRAP_COMPLETE_TOKEN, script)

class PreseedInitrdInjectionTests(BaseVmctlTestCase):
    def _make_seed_initrd(self, dest: Path) -> bytes:
        """Build a minimal valid cpio.gz initrd with a single existing file."""
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as workdir:
            workpath = Path(workdir)
            (workpath / "init").write_text("#!/bin/sh\necho original\n", encoding="utf-8")
            find_proc = subprocess.run(
                ["find", ".", "-mindepth", "1"],
                cwd=workpath, capture_output=True, check=True,
            )
            cpio_proc = subprocess.run(
                ["cpio", "-o", "-H", "newc", "--quiet"],
                input=find_proc.stdout, cwd=workpath,
                capture_output=True, check=True,
            )
            initrd_bytes = gzip.compress(cpio_proc.stdout)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(initrd_bytes)
            return initrd_bytes

    def test_inject_files_into_initrd_merges_into_single_cpio_gz(self):
        initrd_path = self.root / "artifacts/testvm/preseed/initrd"
        original = self._make_seed_initrd(initrd_path)
        vmctl.preseed._inject_files_into_initrd(initrd_path, {
            "preseed.cfg": "d-i preseed/locale string it_IT.UTF-8\n",
            "late_command.sh": "#!/bin/sh\necho hi\n",
        })
        new_data = initrd_path.read_bytes()
        self.assertNotEqual(new_data, original)
        self.assertTrue(new_data.startswith(b"\x1f\x8b"))
        decompressed = gzip.decompress(new_data)
        # Single coherent cpio newc: confirm it ends with a single TRAILER!!! marker.
        self.assertEqual(decompressed.count(b"TRAILER!!!"), 1)
        # Files we injected and the pre-existing one must all be in the archive.
        self.assertIn(b"preseed.cfg", decompressed)
        self.assertIn(b"late_command.sh", decompressed)
        self.assertIn(b"init", decompressed)
        self.assertIn(b"d-i preseed/locale", decompressed)

    def test_extract_preseed_boot_artifacts_injects_into_initrd(self):
        self.vm_config["preseed_config"] = {
            "hostname": "debian-test",
            "username": "tester",
            "password": "s3cret",
        }
        artifact_dir = self.root / "artifacts/testvm/preseed"

        def fake_extract(iso_path, member, dest, dry_run=False):
            dest.parent.mkdir(parents=True, exist_ok=True)
            if member.endswith("vmlinuz"):
                dest.write_bytes(b"FAKE_KERNEL")
            else:
                self._make_seed_initrd(dest)

        with mock.patch.object(vmctl.preseed.iso, "extract_iso_member", side_effect=fake_extract):
            kernel_path, initrd_path = vmctl.preseed.extract_preseed_boot_artifacts(
                self.vm_name, self.vm_config, Path("/tmp/fake.iso"), dry_run=False,
            )

        self.assertEqual(kernel_path, artifact_dir / "vmlinuz")
        self.assertEqual(initrd_path, artifact_dir / "initrd")
        decompressed = gzip.decompress(initrd_path.read_bytes())
        self.assertEqual(decompressed.count(b"TRAILER!!!"), 1)
        self.assertIn(b"preseed.cfg", decompressed)
        self.assertIn(b"late_command.sh", decompressed)


if __name__ == "__main__":
    unittest.main()
