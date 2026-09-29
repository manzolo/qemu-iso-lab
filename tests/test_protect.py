"""vmctl protect: a disk the lab's own destructive commands refuse (local.json "protected")."""
import argparse
import io
import json
from contextlib import redirect_stdout
from unittest import mock

from tests._common import BaseVmctlTestCase
from vmctl import catalog, config, lifecycle, vmstate
from vmctl.errors import VMError


class ProtectTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.write_extra_profile("more.json", {"vms": {
            "other": {**self.vm_config, "name": "Other VM", "disk": {**self.vm_config["disk"], "path": "artifacts/other/disk.qcow2"}},
        }})
        self.cfg = config.load_config()

    def disk(self, name, data=True):
        base = self.root / "artifacts" / name
        base.mkdir(parents=True, exist_ok=True)
        path = base / "disk.qcow2"
        path.write_bytes(b"\1" * (vmstate.DATA_MIN_BYTES + 4096 if data else 1024))
        return path

    def test_the_list_lives_in_local_json_next_to_my_vms(self):
        self.assertEqual(catalog.protected(), [])
        catalog.update("add", ["other"], self.cfg)  # My VMs
        result = catalog.update_protected("add", [self.vm_name], self.cfg)
        self.assertEqual(result["added"], [self.vm_name])
        document = json.loads(catalog.local_path().read_text())
        self.assertEqual(document["protected"], {"vms": [self.vm_name]})
        self.assertEqual(document["catalog"], {"selected": ["other"]})  # untouched
        self.assertEqual(vmstate.protected_names(), {self.vm_name})
        self.assertEqual(config.load_config()["vms"][self.vm_name]["name"], self.vm_config["name"])  # the loader ignores it
        with self.assertRaisesRegex(VMError, "Not in the catalog"):
            catalog.update_protected("add", ["nope"], self.cfg)
        catalog.update_protected("remove", [self.vm_name], self.cfg)
        self.assertNotIn("protected", json.loads(catalog.local_path().read_text()))

    def test_clean_refuses_a_protected_vm_and_clean_all_skips_it(self):
        mine, other = self.disk(self.vm_name), self.disk("other")
        catalog.update_protected("add", [self.vm_name], self.cfg)
        with mock.patch.object(lifecycle, "cmd_stop") as stop:
            with self.assertRaisesRegex(VMError, "is protected: refusing to delete its disk"):
                lifecycle.cmd_clean(argparse.Namespace(vm=self.vm_name, all=False, dry_run=False, checkpoints=False, remove_profile=False))
            stop.assert_not_called()  # refused before the force-stop, not after it
            out = io.StringIO()
            with redirect_stdout(out):
                lifecycle.cmd_clean(argparse.Namespace(vm=None, all=True, dry_run=False, checkpoints=False, remove_profile=False))
        self.assertTrue(mine.exists())
        self.assertFalse(other.exists())
        self.assertIn(f"{self.vm_name}: protected, left as it is", out.getvalue())

    def test_a_new_install_refuses_a_protected_disk_with_data_only(self):
        catalog.update_protected("add", [self.vm_name], self.cfg)
        path = self.disk(self.vm_name, data=False)
        vmstate.begin_install(self.vm_name, "bootstrap-preseed")  # an empty disk: nothing to lose
        path.write_bytes(b"\1" * (vmstate.DATA_MIN_BYTES + 4096))
        with self.assertRaisesRegex(VMError, "refusing to install over its disk"):
            vmstate.begin_install(self.vm_name, "bootstrap-preseed")
        vmstate.begin_install(self.vm_name, "bootstrap-preseed", dry_run=True)  # a dry run touches nothing
        vmstate.begin_install("other", "bootstrap-preseed")  # not protected

    def test_checkpoint_restore_refuses_a_protected_vm(self):
        catalog.update_protected("add", [self.vm_name], self.cfg)
        with self.assertRaisesRegex(VMError, "replace its disk with a checkpoint"):
            lifecycle.cmd_checkpoint(argparse.Namespace(action="restore", vm=self.vm_name, name="before", dry_run=False, yes=True))

    def test_check_vms_without_restore_moves_a_protected_vm_aside_instead_of_cleaning_it(self):
        self.disk(self.vm_name)
        catalog.update_protected("add", [self.vm_name], self.cfg)
        with mock.patch.object(lifecycle, "local_test_clean_candidates", return_value=[self.vm_name, "other"]):
            args = argparse.Namespace(clean_first=True, no_clean_first=False, dry_run=False)
            with mock.patch.object(lifecycle, "cmd_stop"), mock.patch.object(lifecycle, "clean_vm") as clean:
                lifecycle.maybe_clean_local_test_candidates([self.vm_name, "other"], self.cfg, args)
        self.assertEqual([call.args[0] for call in clean.call_args_list], ["other"])

    def test_the_cli_lists_protects_and_unprotects(self):
        out = io.StringIO()
        with redirect_stdout(out):
            lifecycle.cmd_protect(argparse.Namespace(command="protect", vms=[self.vm_name], json=False, dry_run=False))
            lifecycle.cmd_protect(argparse.Namespace(command="protect", vms=[], json=True, dry_run=False))
        self.assertIn(f"{self.vm_name}: protected", out.getvalue())
        self.assertIn(f'"{self.vm_name}"', out.getvalue())
        with redirect_stdout(io.StringIO()):
            lifecycle.cmd_protect(argparse.Namespace(command="unprotect", vms=[self.vm_name], json=False, dry_run=False))
        self.assertEqual(catalog.protected(), [])
        with self.assertRaisesRegex(VMError, "needs at least one"):
            lifecycle.cmd_protect(argparse.Namespace(command="unprotect", vms=[], json=False, dry_run=False))
