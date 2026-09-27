"""My VMs: the personal selection of the catalog, kept in local.json under "catalog"."""
import argparse
import io
import json
from contextlib import redirect_stdout

from tests._common import BaseVmctlTestCase
from vmctl import catalog, config, lifecycle, ui
from vmctl.errors import VMError


class CatalogTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.write_extra_profile("more.json", {"vms": {
            "other": {**self.vm_config, "name": "Other VM", "disk": {**self.vm_config["disk"], "path": "artifacts/other/disk.qcow2"}},
            "third": {**self.vm_config, "name": "Third VM", "disk": {**self.vm_config["disk"], "path": "artifacts/third/disk.qcow2"}},
        }})
        self.cfg = config.load_config()

    def local(self):
        return json.loads(catalog.local_path().read_text())

    def test_the_selection_lives_next_to_vms_and_survives_an_empty_start(self):
        self.assertEqual(catalog.selected(), [])
        self.assertFalse(catalog.local_path().exists())
        result = catalog.update("add", ["other", self.vm_name, "other"], self.cfg)
        self.assertEqual(result, {"selected": ["other", self.vm_name], "added": ["other", self.vm_name], "removed": []})
        self.assertEqual(self.local(), {"vms": {}, "catalog": {"selected": ["other", self.vm_name]}})
        self.assertEqual(catalog.selected(), ["other", self.vm_name])
        # Order is the order of choice; a name already there is not moved and nothing is rewritten.
        before = catalog.local_path().stat().st_mtime_ns
        self.assertEqual(catalog.update("add", ["other"], self.cfg)["added"], [])
        self.assertEqual(catalog.local_path().stat().st_mtime_ns, before)
        self.assertEqual(catalog.update("add", ["third"], self.cfg)["selected"], ["other", self.vm_name, "third"])

    def test_other_keys_of_local_json_are_kept_and_a_backup_is_written(self):
        catalog.local_path().write_text(json.dumps({"note": "keep me", "vms": {self.vm_name: {"cpus": 2}}}))
        original = catalog.local_path().read_bytes()
        catalog.update("set", ["third"], self.cfg)
        self.assertEqual(self.local(), {"note": "keep me", "vms": {self.vm_name: {"cpus": 2}}, "catalog": {"selected": ["third"]}})
        self.assertEqual(catalog.local_path().with_suffix(".json.bak").read_bytes(), original)
        self.assertEqual(config.load_config()["vms"][self.vm_name]["cpus"], 2)  # the loader ignores the key
        # An empty selection means the whole catalog: the key goes away, the rest stays.
        self.assertEqual(catalog.update("remove", ["third"], self.cfg)["removed"], ["third"])
        self.assertEqual(self.local(), {"note": "keep me", "vms": {self.vm_name: {"cpus": 2}}})
        catalog.update("add", ["other", "third"], self.cfg)
        self.assertEqual(catalog.update("clear", [], self.cfg), {"selected": [], "added": [], "removed": ["other", "third"]})
        self.assertNotIn("catalog", self.local())

    def test_names_are_checked_against_the_catalog_and_aliases_resolve(self):
        with self.assertRaisesRegex(VMError, "Not in the catalog: nope"):
            catalog.update("add", ["other", "nope"], self.cfg)
        self.assertFalse(catalog.local_path().exists())
        with self.assertRaisesRegex(VMError, "needs at least one"):
            catalog.update("add", [], self.cfg)
        with self.assertRaisesRegex(VMError, "Unknown catalog action"):
            catalog.update("toggle", ["other"], self.cfg)
        alias, canonical = next(iter(config.PROFILE_ALIASES.items()))
        cfg = {"vms": {**self.cfg["vms"], canonical: self.vm_config}}
        self.assertEqual(catalog.update("add", [alias], cfg)["selected"], [canonical])
        catalog.local_path().write_text(json.dumps({"vms": {}, "catalog": {"selected": [alias, canonical]}}))
        self.assertEqual(catalog.selected(), [canonical])

    def test_a_malformed_catalog_key_is_reported_not_ignored(self):
        for broken in ({"selected": "other"}, ["other"], {"selected": [1]}):
            catalog.local_path().write_text(json.dumps({"vms": {}, "catalog": broken}))
            with self.subTest(broken=broken), self.assertRaisesRegex(VMError, "'catalog' must be an object"):
                catalog.selected()

    def run_catalog(self, action="list", vms=(), names=False, json_out=False, dry_run=False):
        args = argparse.Namespace(action=action, vms=list(vms), json=json_out, names=names, dry_run=dry_run)
        output = io.StringIO()
        with redirect_stdout(output):
            code = lifecycle.cmd_catalog(args)
        self.assertEqual(code, 0)
        return output.getvalue()

    def test_the_command_lists_adds_removes_and_reports_names_that_left_the_catalog(self):
        ui.USE_COLOR = False
        self.assertIn("nothing chosen", self.run_catalog())
        text = self.run_catalog("add", ["other", self.vm_name])
        self.assertIn("other: added to My VMs", text)
        self.assertIn(f"My VMs: other {self.vm_name}", text)
        self.assertEqual(self.run_catalog(names=True).splitlines(), ["other", self.vm_name])
        listing = self.run_catalog()
        self.assertIn("My VMs (2 of 3 profiles)", listing)
        self.assertIn("Other VM", listing)
        # A cleaned clone leaves a dangling name: listed as such, and removable.
        catalog.local_path().write_text(json.dumps({"vms": {}, "catalog": {"selected": ["other", "gone-clone"]}}))
        self.assertEqual(self.run_catalog(names=True).splitlines(), ["other"])
        self.assertIn("not in the catalog any more", self.run_catalog())
        self.assertEqual(json.loads(self.run_catalog(json_out=True)), {"selected": ["other", "gone-clone"], "missing": ["gone-clone"]})
        with self.assertRaisesRegex(VMError, "takes no profile names"):
            self.run_catalog("list", ["other"])
        self.assertIn("Nothing changed", self.run_catalog("remove", ["third"]))
        self.assertIn("would clear", self.run_catalog("clear", dry_run=True))
        self.assertIn("My VMs is empty", self.run_catalog("clear"))

    def test_list_mine_shows_the_selection_only_a_disk_does_not_count(self):
        catalog.update("add", ["third"], self.cfg)
        self.create_disk()
        output = io.StringIO()
        with redirect_stdout(output):
            lifecycle.cmd_list(argparse.Namespace(mine=True, names=True, json=False, groups=False))
        self.assertEqual(output.getvalue().split(), ["third"])
