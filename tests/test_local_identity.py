"""The guest identity of local.json: vmctl identity and the web's Welcome / Identity panel."""
import argparse
import io
import json
from contextlib import redirect_stdout

from tests._common import BaseVmctlTestCase
from vmctl import catalog, config, local_identity
from vmctl.errors import VMError


class Sha512CryptTests(BaseVmctlTestCase):
    def test_matches_openssl_passwd_6(self):
        # openssl passwd -6 -salt saltsalt secret, measured 2026-09-29
        self.assertEqual(local_identity.sha512_crypt("secret", "saltsalt"),
                         "$6$saltsalt$TVLlQcbpFVof5W3Yz4DTP6gRstiNuHwwTt6GLc1E5n0U0aDehy0S5knV8wiOQSpT0Y77vwPZN.Pq.H91p5hVO1")
        # openssl passwd -6 -salt 0123456789abcdef <70 x 'a'>: a key longer than one digest block
        self.assertEqual(local_identity.sha512_crypt("a" * 70, "0123456789abcdef")[:20], "$6$0123456789abcdef$")
        self.assertEqual(local_identity.sha512_crypt("lab", "abc"),
                         local_identity.sha512_crypt("lab", "abc"))
        random = local_identity.sha512_crypt("lab")
        self.assertRegex(random, r"^\$6\$[./0-9A-Za-z]{16}\$[./0-9A-Za-z]{86}$")
        with self.assertRaises(VMError):
            local_identity.sha512_crypt("lab", "bad salt!")


class IdentityFileTests(BaseVmctlTestCase):
    def test_a_fresh_checkout_has_no_file_and_the_defaults_are_lab(self):
        info = local_identity.read()
        self.assertFalse(info["exists"])
        self.assertEqual(info["identity"], {"user": "", "realname": "", "has_password": False, "has_hash": False})
        self.assertEqual(info["defaults"]["user"], "lab")

    def test_save_creates_the_file_with_the_hash_and_moves_the_profiles(self):
        self.write_extra_profile("more.json", {"vms": {"withssh": {**self.vm_config, "name": "SSH VM", "ssh_provision": {"user": "lab"},
                                                                    "disk": {**self.vm_config["disk"], "path": "artifacts/withssh/disk.qcow2"}}}})
        result = local_identity.save("tester", "s3cret", "Test User")
        self.assertTrue(result["exists"])
        self.assertEqual(result["identity"], {"user": "tester", "realname": "Test User", "has_password": True, "has_hash": True})
        document = json.loads(catalog.local_path().read_text())
        self.assertEqual(document["identity"]["user"], "tester")
        self.assertEqual(document["identity"]["password"], "s3cret")
        self.assertTrue(document["identity"]["password_hash"].startswith("$6$"))
        self.assertEqual(document["vms"], {})
        self.assertEqual(config.load_config()["vms"]["withssh"]["ssh_provision"]["user"], "tester")

    def test_the_hash_only_when_the_password_must_not_be_stored(self):
        local_identity.save("tester", "s3cret", store_password=False)
        identity = json.loads(catalog.local_path().read_text())["identity"]
        self.assertNotIn("password", identity)
        self.assertIn("password_hash", identity)

    def test_an_edit_keeps_the_credentials_and_every_other_key(self):
        local_identity.save("tester", "s3cret", "Test User")
        catalog.update("add", [self.vm_name], config.load_config())  # My VMs
        catalog.update_protected("add", [self.vm_name], config.load_config())
        before = json.loads(catalog.local_path().read_text())
        result = local_identity.save("tester2", "", "Other Name")
        after = json.loads(catalog.local_path().read_text())
        self.assertEqual(result["identity"]["user"], "tester2")
        self.assertEqual(after["identity"]["password_hash"], before["identity"]["password_hash"])
        self.assertEqual(after["identity"]["password"], "s3cret")
        self.assertEqual(after["identity"]["realname"], "Other Name")
        self.assertEqual(after["catalog"], before["catalog"])
        self.assertEqual(after["protected"], before["protected"])
        self.assertTrue(catalog.local_path().with_suffix(".json.bak").exists())

    def test_the_locale_block_is_saved_next_to_the_identity_and_empty_values_drop_keys(self):
        result = local_identity.save("tester", "s3cret", locale={"language": "it_IT.UTF-8", "keyboard": "it", "timezone": "Europe/Rome"})
        self.assertEqual(result["locale"], {"language": "it_IT.UTF-8", "keyboard": "it", "timezone": "Europe/Rome"})
        self.assertEqual(json.loads(catalog.local_path().read_text())["locale"], {"language": "it_IT.UTF-8", "keyboard": "it", "timezone": "Europe/Rome"})
        result = local_identity.save("tester", "", locale={"keyboard": ""})  # one key dropped, the others kept
        self.assertEqual(result["locale"], {"language": "it_IT.UTF-8", "keyboard": "", "timezone": "Europe/Rome"})
        result = local_identity.save("tester", "", locale={"language": "", "timezone": ""})
        self.assertNotIn("locale", json.loads(catalog.local_path().read_text()))
        with self.assertRaisesRegex(VMError, "language"):
            local_identity.save("tester", "", locale={"language": "italiano"})
        self.assertEqual(local_identity.read()["defaults"]["language"], "en_US.UTF-8")

    def test_refuses_a_bad_user_name_and_a_first_save_without_a_password(self):
        for bad in ("", "Root", "a b", "x" * 33, "1abc"):
            with self.subTest(user=bad), self.assertRaisesRegex(VMError, "POSIX login name"):
                local_identity.save(bad, "x")
        with self.assertRaisesRegex(VMError, "password is needed"):
            local_identity.save("tester", "")
        self.assertFalse(catalog.local_path().exists())

    def test_the_command_shows_sets_and_never_prints_the_secrets(self):
        out = io.StringIO()
        with redirect_stdout(out):
            local_identity.cmd_identity(argparse.Namespace(user=None, password=None, ask_password=False, realname=None, json=False))
        self.assertIn("No vms/profiles/local.json yet", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            local_identity.cmd_identity(argparse.Namespace(user="tester", password="s3cret", ask_password=False, realname="Test", json=False))
            local_identity.cmd_identity(argparse.Namespace(user=None, password=None, ask_password=False, realname=None, json=True))
        text = out.getvalue()
        self.assertIn("user tester", text)
        self.assertNotIn("s3cret", text)
        self.assertNotIn("$6$", text)
        self.assertEqual(json.loads(text[text.index("{"):])["identity"]["user"], "tester")
