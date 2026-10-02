"""Profile versions: meta.version, the lock, the bump tool and what state.json records."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests._common import BaseVmctlTestCase
from tools import bump_profile
from vmctl import config, profile_versions, vmstate
from vmctl.errors import VMError

ROOT = Path(__file__).resolve().parents[1]


class FingerprintTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config["meta"] = {"status": "manual", "version": "1.0.0"}
        self.write_config_dir()
        self.files = self.root / "vms" / "profile-files" / "testvm"
        self.files.mkdir(parents=True)
        (self.files / "rc").write_text("one")

    def entry(self, **changes):
        return {**self.vm_config, **changes}

    def test_labels_and_metadata_do_not_change_the_fingerprint_but_the_recipe_and_its_files_do(self):
        base = profile_versions.fingerprint(self.entry(), self.root)
        self.assertEqual(profile_versions.fingerprint(self.entry(name="Renamed", meta={"verified": "2026-09-27", "version": "9.9.9"}, iso_help="x"), self.root), base)
        self.assertNotEqual(profile_versions.fingerprint(self.entry(memory_mb=4096), self.root), base)
        with_file = self.entry(ssh_provision={"user": "lab", "ssh_host_port": 2222, "copy_from_host": [{"source": "vms/profile-files/testvm/rc", "dest": "/tmp/rc"}]})
        one = profile_versions.fingerprint(with_file, self.root)
        self.assertNotEqual(one, base)
        (self.files / "rc").write_text("two")
        self.assertNotEqual(profile_versions.fingerprint(with_file, self.root), one)
        self.assertEqual(profile_versions.referenced_files(with_file), ["vms/profile-files/testvm/rc"])
        lab_file = {"ssh_provision": {"copy_from_host": [{"source": "vms/labs/x-lab/provision/setup.sh"}, {"source": "/home/me/own.sh"}]}}
        self.assertEqual(profile_versions.referenced_files(lab_file), ["vms/labs/x-lab/provision/setup.sh"])
        # A user's own path is not part of the catalog.
        theirs = self.entry(cloud_init={"copy_from_host": [{"source": "~/.config/niri", "dest": "/home/{{user}}/.config/niri"}]})
        self.assertEqual(profile_versions.referenced_files(theirs), [])


class LockAndBumpTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config["meta"] = {"status": "manual"}
        self.write_config_dir()
        self.write_extra_profile("local.json", {"vms": {"mine-only": self.vm_config}})  # never versioned

    def profile_meta(self, name=None):
        document = json.loads((self.config_dir / "profiles" / "test.json").read_text())
        return document["vms"][name or self.vm_name]["meta"]

    def test_init_stamps_1_0_0_records_the_lock_and_check_agrees(self):
        self.assertIn("meta.version missing", profile_versions.check(self.root)[0])
        self.assertEqual(profile_versions.init("first", self.root, date="2026-09-27"), [self.vm_name])
        self.assertEqual(self.profile_meta(), {"status": "manual", "version": "1.0.0"})
        lock = profile_versions.read_lock(self.root)
        self.assertEqual(list(lock["profiles"]), [self.vm_name])
        self.assertEqual(lock["profiles"][self.vm_name]["history"], [{"version": "1.0.0", "date": "2026-09-27", "note": "first"}])
        self.assertEqual(profile_versions.check(self.root), [])
        self.assertEqual(profile_versions.init("again", self.root), [])
        self.assertEqual(profile_versions.catalog_version(self.vm_name, self.root), "1.0.0")
        self.assertIsNone(profile_versions.catalog_version("mine-only", self.root))
        self.assertTrue((self.root / "vms" / "profiles.lock").exists())
        self.assertEqual(config.load_config()["vms"][self.vm_name]["meta"]["version"], "1.0.0")

    def test_a_changed_recipe_fails_the_check_until_a_bump_records_the_change(self):
        profile_versions.init("first", self.root, date="2026-09-27")
        self.vm_config["memory_mb"] = 4096
        self.vm_config["meta"] = {"status": "manual", "version": "1.0.0"}
        self.write_config_dir()
        problems = profile_versions.check(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn(f"{self.vm_name}: the recipe changed since 1.0.0", problems[0])
        self.assertEqual(profile_versions.bump(self.vm_name, "patch", "more RAM", self.root, date="2026-09-28"), ("1.0.0", "1.0.1"))
        self.assertEqual(self.profile_meta()["version"], "1.0.1")
        self.assertEqual(profile_versions.check(self.root), [])
        self.assertEqual([h["version"] for h in profile_versions.history(self.vm_name, self.root)], ["1.0.1", "1.0.0"])
        self.assertEqual(profile_versions.bump(self.vm_name, "minor", "a shared folder", self.root), ("1.0.1", "1.1.0"))
        self.assertEqual(profile_versions.bump(self.vm_name, "major", "new user", self.root), ("1.1.0", "2.0.0"))
        # A version edited by hand, a vanished profile and a missing note are all reported.
        self.vm_config["meta"]["version"] = "2.0.5"
        self.write_config_dir()
        self.assertIn("meta.version 2.0.5 but", profile_versions.check(self.root)[0])
        with self.assertRaisesRegex(VMError, "needs a note"):
            profile_versions.bump(self.vm_name, "patch", "  ", self.root)
        with self.assertRaisesRegex(VMError, "not a tracked profile"):
            profile_versions.bump("mine-only", "patch", "x", self.root)
        with self.assertRaisesRegex(VMError, "part must be"):
            profile_versions.bump(self.vm_name, "huge", "x", self.root)
        lock = profile_versions.read_lock(self.root)
        lock["profiles"]["gone"] = {"version": "1.0.0", "fingerprint": "x", "history": []}
        profile_versions.write_lock(lock, self.root)
        self.assertIn("gone: in vms/profiles.lock but not in the catalog", profile_versions.check(self.root)[-1])
        self.assertEqual(profile_versions.prune(self.root), ["gone"])

    def test_the_tool_drives_the_same_functions(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(bump_profile.main(["--init", "--root", str(self.root)]), 0)
            self.assertEqual(bump_profile.main(["--check", "--root", str(self.root)]), 0)
            self.assertEqual(bump_profile.main([self.vm_name, "patch", "-m", "fix", "--root", str(self.root)]), 0)
            self.assertEqual(bump_profile.main(["--history", self.vm_name, "--root", str(self.root)]), 0)
            self.assertEqual(bump_profile.main(["--prune", "--root", str(self.root)]), 0)
        text = out.getvalue()
        self.assertIn("1 profile(s) recorded", text)
        self.assertIn("catalog and lock agree", text)
        self.assertIn(f"{self.vm_name}: 1.0.0 -> 1.0.1", text)
        self.assertIn("1.0.1      ", text)
        self.assertIn("0 lock entries removed", text)
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(bump_profile.main([self.vm_name, "patch", "--root", str(self.root)]), 1)
        self.assertIn("needs a note", err.getvalue())

    def test_a_bad_version_is_refused_by_the_loader(self):
        self.vm_config["meta"]["version"] = "1.0"
        self.write_config_dir()
        with self.assertRaisesRegex(VMError, "meta.version must be MAJOR.MINOR.PATCH"):
            config.load_config()


class StateRecordTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config["meta"] = {"status": "unattended", "version": "1.0.0"}
        self.write_config_dir()
        profile_versions.init("first", self.root)

    def test_an_install_records_the_catalog_version_and_the_summary_compares_it(self):
        vmstate.begin_install(self.vm_name, "bootstrap-preseed")
        record = vmstate.load(self.vm_name)
        self.assertEqual(record["install"]["profile_version"], "1.0.0")
        self.assertRegex(record["install"]["vmctl_version"], r"^\d+\.\d+\.\d+$")
        vmstate.complete_install(self.vm_name, "bootstrap-preseed")
        self.create_disk().write_bytes(b"\xff" * (vmstate.DATA_MIN_BYTES + 1))
        with mock.patch("shutil.which", return_value=None):
            known = vmstate.summary(self.vm_name, config.load_config()["vms"][self.vm_name])
        self.assertEqual((known["profile_version"], known["catalog_version"]), ("1.0.0", "1.0.0"))
        self.assertIn("(profile 1.0.0)", known["detail"])
        profile_versions.bump(self.vm_name, "patch", "a fix", self.root)
        with mock.patch("shutil.which", return_value=None):
            known = vmstate.summary(self.vm_name, config.load_config()["vms"][self.vm_name])
        self.assertEqual(known["catalog_version"], "1.0.1")
        self.assertIn("(profile 1.0.0, the catalog is at 1.0.1 now)", known["detail"])
        # A completion without its start (a hand-driven flow) still says which recipe it was.
        vmstate.complete_install(self.vm_name, "provision")
        self.assertEqual(vmstate.load(self.vm_name)["install"]["profile_version"], "1.0.1")
        # A clone or a local-only VM has no catalog version: nothing is claimed.
        self.write_extra_profile("local.json", {"vms": {"mine-only": self.vm_config}})
        vmstate.begin_install("mine-only", "bootstrap-preseed")
        self.assertIsNone(vmstate.load("mine-only")["install"]["profile_version"])


class CatalogConsistencyTests(BaseVmctlTestCase):
    def test_every_tracked_profile_is_versioned_and_the_lock_matches_the_catalog(self):
        # The real catalog (tracked files only, never local.json): a recipe edited without a bump
        # fails here and names the profile and the command to run.
        self.assertEqual(profile_versions.check(ROOT), [])
