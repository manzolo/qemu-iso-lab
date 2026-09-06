import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.errors  # noqa: E402
import vmctl.ssh  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class ConfiguredSshKeyTests(BaseVmctlTestCase):
    """A profile may point ssh_key at a personal key: vmctl must refuse one it could never use."""

    def _key(self) -> Path:
        key = self.root / "keys" / "id_rsa"
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nx\n-----END OPENSSH PRIVATE KEY-----\n", encoding="utf-8")
        (self.root / "keys" / "id_rsa.pub").write_text("ssh-rsa AAAA test\n", encoding="utf-8")
        return key

    def test_passphrase_protected_key_fails_fast_with_a_clear_message(self):
        key = self._key()
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2299, "ssh_key": str(key)}
        cfg = self.vm_config["ssh_provision"]
        with mock.patch.object(vmctl.ssh, "key_needs_passphrase", return_value=True), self.assertRaises(vmctl.errors.VMError) as ctx:
            vmctl.ssh.resolve_ssh_private_key(self.vm_config, cfg)
        self.assertIn("passphrase-protected", str(ctx.exception))
        self.assertIn("BatchMode", str(ctx.exception))
        # dry-run only plans: no probing of the key material
        with mock.patch.object(vmctl.ssh, "key_needs_passphrase", return_value=True):
            self.assertEqual(vmctl.ssh.resolve_ssh_private_key(self.vm_config, cfg, dry_run=True), key)

    def test_plain_key_is_returned_and_missing_key_still_errors(self):
        key = self._key()
        cfg = {"user": "lab", "ssh_host_port": 2299, "ssh_key": str(key)}
        self.vm_config["ssh_provision"] = cfg
        with mock.patch.object(vmctl.ssh, "key_needs_passphrase", return_value=False):
            self.assertEqual(vmctl.ssh.resolve_ssh_private_key(self.vm_config, cfg), key)
        cfg["ssh_key"] = str(self.root / "keys" / "missing")
        with self.assertRaises(vmctl.errors.VMError) as ctx:
            vmctl.ssh.resolve_ssh_private_key(self.vm_config, cfg)
        self.assertIn("not found", str(ctx.exception))

    def test_key_needs_passphrase_uses_ssh_keygen_when_available(self):
        key = self._key()
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(vmctl.ssh.key_needs_passphrase(key))
        completed = mock.Mock(returncode=255, stderr="Load key: incorrect passphrase supplied to decrypt private key", stdout="")
        with mock.patch("shutil.which", return_value="/usr/bin/ssh-keygen"), mock.patch.object(vmctl.ssh.subprocess, "run", return_value=completed):
            self.assertTrue(vmctl.ssh.key_needs_passphrase(key))
        completed = mock.Mock(returncode=0, stderr="", stdout="ssh-rsa AAAA")
        with mock.patch("shutil.which", return_value="/usr/bin/ssh-keygen"), mock.patch.object(vmctl.ssh.subprocess, "run", return_value=completed):
            self.assertFalse(vmctl.ssh.key_needs_passphrase(key))


if __name__ == "__main__":
    unittest.main()
