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

    def test_a_starred_vm_with_a_disk_is_protected_while_the_star_is_on_it(self):
        catalog.update("add", [self.vm_name, "other"], self.cfg)  # My VMs
        self.disk("other", data=False)
        self.assertIsNone(vmstate.protection_reason(self.vm_name))  # no disk yet
        self.assertIsNone(vmstate.protection_reason("other"))  # an empty disk: nothing to lose
        self.disk(self.vm_name)
        self.assertEqual(vmstate.protection_reason(self.vm_name), "star")
        self.assertEqual(vmstate.protected_names(), {self.vm_name})
        with self.assertRaisesRegex(VMError, "in My VMs and its disk holds data.*vmctl catalog remove"):
            vmstate.refuse_if_protected(self.vm_name, "delete its disk")
        with self.assertRaisesRegex(VMError, "vmctl catalog remove"):
            vmstate.begin_install(self.vm_name, "bootstrap-preseed")
        # vmctl unprotect cannot undo it: the star can
        out = io.StringIO()
        with redirect_stdout(out):
            lifecycle.cmd_protect(argparse.Namespace(command="unprotect", vms=[self.vm_name], json=False, dry_run=False))
            lifecycle.cmd_protect(argparse.Namespace(command="protect", vms=[], json=True, dry_run=False))
        self.assertIn("still protected, it is in My VMs", out.getvalue())
        self.assertEqual(json.loads(out.getvalue()[out.getvalue().index("{"):]), {"protected": [self.vm_name], "flagged": [], "starred": [self.vm_name]})
        catalog.update("remove", [self.vm_name], self.cfg)
        self.assertIsNone(vmstate.protection_reason(self.vm_name))
        catalog.update_protected("add", [self.vm_name], self.cfg)
        self.assertEqual(vmstate.protection_reason(self.vm_name), "flag")  # the flag wins in the wording

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


class RowCleanupTests(BaseVmctlTestCase):
    """check-vms gives each row's disk back when the row ends (2026-09-29: a full matrix filled the disk)."""

    def setUp(self):
        super().setUp()
        self.cfg = config.load_config()

    def install(self, name):
        base = self.root / "artifacts" / name
        base.mkdir(parents=True, exist_ok=True)
        (base / "disk.qcow2").write_bytes(b"TEST-INSTALL")
        return base

    def cleanup(self, stashed=None, **flags):
        args = argparse.Namespace(dry_run=False, **flags)
        with mock.patch.object(lifecycle.pvecluster, "clusters", return_value={"c": {"nodes": ["node1", "node2"]}}):
            return lifecycle.RowCleanup(stashed or {}, ["fresh", "stashed", "node1", "node2"], self.cfg,
                                        ["fresh", "stashed", "node1", "node2", "boot"], args)

    def test_each_row_removes_its_install_and_a_stashed_vm_gets_its_own_back(self):
        backup = self.root / "backup" / "stashed"
        backup.mkdir(parents=True)
        (backup / "disk.qcow2").write_bytes(b"ORIGINAL")
        cleanup = self.cleanup({"stashed": str(backup)})
        fresh, stashed, boot = self.install("fresh"), self.install("stashed"), self.install("boot")
        with mock.patch.object(lifecycle, "cmd_stop"):
            cleanup.row_done("fresh", "passed")
            self.assertFalse(fresh.exists())  # at once, not at the end of the matrix
            cleanup.row_done("stashed", "failed")
            self.assertEqual((stashed / "disk.qcow2").read_bytes(), b"ORIGINAL")
            cleanup.row_done("boot", "passed")  # a boot-check row installs nothing: its disk stays
            self.assertTrue(boot.exists())

    def test_a_row_that_did_not_pass_keeps_its_logs_in_the_report(self):
        report = self.root / "report"
        base = self.install("fresh")
        (base / "logs").mkdir()
        (base / "logs" / "bootstrap-serial.log").write_text("Could not resolve host")
        cleanup = self.cleanup(_report_dir=str(report))
        with mock.patch.object(lifecycle, "cmd_stop"), redirect_stdout(io.StringIO()):
            cleanup.row_done("fresh", "failed")
        self.assertFalse(base.exists())
        self.assertEqual((report / "logs" / "fresh" / "bootstrap-serial.log").read_text(), "Could not resolve host")

    def test_a_stashed_row_that_failed_keeps_its_logs_too(self):
        report, backup = self.root / "report", self.root / "backup" / "stashed"
        backup.mkdir(parents=True)
        base = self.install("stashed")
        (base / "logs").mkdir()
        (base / "logs" / "bootstrap-serial.log").write_text("HTTP 500")
        cleanup = self.cleanup({"stashed": str(backup)}, _report_dir=str(report))
        with mock.patch.object(lifecycle, "cmd_stop"), redirect_stdout(io.StringIO()):
            cleanup.row_done("stashed", "failed")
        self.assertEqual((report / "logs" / "stashed" / "bootstrap-serial.log").read_text(), "HTTP 500")

    def test_keep_and_keep_passed(self):
        with mock.patch.object(lifecycle, "cmd_stop"):
            cleanup = self.cleanup(keep_passed=True)
            passed, failed = self.install("fresh"), self.install("stashed")
            cleanup.row_done("fresh", "passed")
            cleanup.row_done("stashed", "failed")
            self.assertTrue(passed.exists())
            self.assertFalse(failed.exists())
            cleanup = self.cleanup(keep=True)
            kept = self.install("stashed")
            cleanup.row_done("stashed", "failed")
            self.assertTrue(kept.exists())

    def test_a_starred_row_that_passes_keeps_its_fresh_install(self):
        # "fresh" is in My VMs (a star, no disk yet): the matrix installs it and leaves it
        with mock.patch.object(lifecycle, "cmd_stop"), mock.patch.object(vmstate, "starred_names", return_value={"fresh"}):
            cleanup = self.cleanup()
            starred, other = self.install("fresh"), self.install("stashed")
            cleanup.row_done("fresh", "passed")
            cleanup.row_done("stashed", "passed")
            self.assertTrue(starred.exists())
            self.assertFalse(other.exists())
            self.assertEqual(cleanup.kept_starred, ["fresh"])
            cleanup = self.cleanup()
            failed = self.install("fresh")
            cleanup.row_done("fresh", "failed")  # a failed row leaves nothing, star or not
            self.assertFalse(failed.exists())

    def test_cluster_nodes_wait_for_the_cluster_check(self):
        cleanup = self.cleanup()
        node = self.install("node1")
        with mock.patch.object(lifecycle, "cmd_stop"):
            cleanup.row_done("node1", "passed")
            self.assertTrue(node.exists())  # the cluster check still needs this disk
            with redirect_stdout(io.StringIO()):
                cleanup.finish()
        self.assertFalse(node.exists())

    def test_an_interrupted_run_leaves_its_stash_and_the_next_run_puts_it_back(self):
        stash = lifecycle.restore_backup_base()
        (stash / "lost").mkdir(parents=True)
        (stash / "lost" / "disk.qcow2").write_bytes(b"ORIGINAL")
        (stash / "both").mkdir()
        self.install("both")
        out = io.StringIO()
        with redirect_stdout(out):
            lifecycle.recover_orphan_stash()
        self.assertEqual((self.root / "artifacts" / "lost" / "disk.qcow2").read_bytes(), b"ORIGINAL")
        self.assertTrue((stash / "both").exists())  # a directory in the way: both kept, the user chooses
        self.assertIn("choose by hand", out.getvalue())


class DiskAdmissionTests(BaseVmctlTestCase):
    def test_sizes_and_disk_room_decide_admission(self):
        from vmctl import scheduler
        self.assertEqual([scheduler.size_gb(v) for v in ("32G", "512M", "1T", "", "junk", 2 * 1024 ** 3)], [32, 1, 1024, 0, 0, 2])
        free = {"gb": 100}
        s = scheduler.DynamicScheduler(scheduler.HostResources(64000, 64000, 16), disk_source=lambda: free["gb"],
                                       disk_reserve_gb=30, memory_source=lambda: 64000)
        s.disk_free_gb = free["gb"]
        big = scheduler.VmCost(mem_mb=1024, cpus=1, disk_gb=40)
        self.assertTrue(s.fits([], big))
        self.assertFalse(s.fits([big], big))  # 100 - 30 - 40 < 40
        self.assertTrue(s.fits([big], scheduler.VmCost(mem_mb=1024, cpus=1)))  # no disk: nothing to reserve
        ran = s.run([("a", big), ("b", big)], lambda name: name)
        self.assertEqual(sorted(name for name, _ in ran), ["a", "b"])  # the second waited for the first
        self.assertEqual(s.peak_running, 1)


class MatrixLockTests(BaseVmctlTestCase):
    def test_a_second_check_vms_refuses_while_one_runs(self):
        with lifecycle.matrix_lock():
            with self.assertRaisesRegex(VMError, "Another check-vms is running"):
                with lifecycle.matrix_lock():
                    pass
            with lifecycle.matrix_lock(dry_run=True):
                pass  # a dry run neither takes nor needs the lock
        with lifecycle.matrix_lock():
            pass  # released with the run
