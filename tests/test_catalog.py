"""My VMs: the personal selection of the catalog, kept in local.json under "catalog"."""
import argparse
import io
import json
from contextlib import redirect_stdout
from unittest import mock

from tests._common import BaseVmctlTestCase
from vmctl import catalog, config, labs, lifecycle, ui
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

    def test_hidden_profiles_leave_the_lists_and_a_star_and_hidden_exclude_each_other(self):
        from vmctl import tui_bridge
        self.assertEqual(catalog.hidden(), [])
        catalog.update("add", ["other", self.vm_name], self.cfg)
        result = catalog.update_hidden("add", ["other", "third"], self.cfg)
        self.assertEqual(result["added"], ["other", "third"])
        self.assertEqual(self.local()["catalog"], {"selected": [self.vm_name], "hidden": ["other", "third"]})  # other lost its star
        self.assertEqual(catalog.update("add", ["third"], self.cfg)["added"], ["third"])
        self.assertEqual(self.local()["catalog"], {"selected": [self.vm_name, "third"], "hidden": ["other"]})  # the star unhides
        catalog.update_hidden("remove", ["other"], self.cfg)
        self.assertNotIn("hidden", self.local()["catalog"])
        with self.assertRaisesRegex(VMError, "Not in the catalog"):
            catalog.update_hidden("add", ["nope"], self.cfg)
        # the dashboards: a hidden row leaves every list unless it runs or holds a disk; "hidden" lists them
        row = lambda name, **kw: {"name": name, "label": name, "family": "x", "prepared": False, "installed": False, "running": False, "mine": False, "hidden": False, **kw}
        rows = [row("a", hidden=True), row("b", hidden=True, running=True), row("c", hidden=True, installed=True, prepared=True), row("d")]
        self.assertEqual([r["name"] for r in tui_bridge.visible_rows(rows, "", "all")], ["b", "c", "d"])
        self.assertEqual([r["name"] for r in tui_bridge.visible_rows(rows, "", "hidden")], ["b", "c", "a"])
        # My VMs: the selection plus what runs, except a running member of a declared lab (unless starred)
        rows = [row("solo", running=True), row("member", running=True, lab="netlab"), row("starred", mine=True, lab="netlab"), row("idle")]
        self.assertEqual([r["name"] for r in tui_bridge.visible_rows(rows, "", "mine")], ["solo", "starred"])
        self.assertTrue(catalog.in_my_vms(False, True, None))
        self.assertFalse(catalog.in_my_vms(False, True, "netlab"))
        self.assertTrue(catalog.in_my_vms(True, False, "netlab"))
        out = io.StringIO()
        with redirect_stdout(out):
            lifecycle.cmd_catalog(argparse.Namespace(action="hide", vms=["other"], json=False, names=False, dry_run=False))
            lifecycle.cmd_catalog(argparse.Namespace(action="list", vms=[], json=True, names=False, dry_run=False))
            lifecycle.cmd_catalog(argparse.Namespace(action="unhide", vms=["other"], json=False, names=False, dry_run=False))
        text = out.getvalue()
        self.assertIn("other: hidden from the lists", text)
        self.assertEqual(json.loads(text[text.index("{"):text.index("}") + 1])["hidden"], ["other"])
        self.assertIn("other: shown again", text)

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
        self.assertEqual(json.loads(self.run_catalog(json_out=True)), {"selected": ["other", "gone-clone"], "missing": ["gone-clone"], "hidden": []})
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

    def test_list_mine_leaves_a_running_lab_member_to_the_labs_view(self):
        running = {"other", "third"}
        with mock.patch.object(lifecycle, "running_qemu_pid", side_effect=lambda name, vm: 1 if name in running else None), \
             mock.patch.object(labs, "lab_of", return_value={"third": "some-lab"}):
            output = io.StringIO()
            with redirect_stdout(output):
                lifecycle.cmd_list(argparse.Namespace(mine=True, names=True, json=False, groups=False))
        self.assertEqual(output.getvalue().split(), ["other"])
