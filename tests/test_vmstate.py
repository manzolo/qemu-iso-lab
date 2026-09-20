"""artifacts/<vm>/state.json: what the host records about the disk it holds."""

import argparse
import io
import json
import shutil
from unittest import mock

import vmctl.lifecycle
import vmctl.runtime
import vmctl.vmstate
from _common import BaseVmctlTestCase


class VmstateRecordTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.write_config_dir()
        self.vm = self.vmctl.get_vm(self.vmctl.load_config(), self.vm_name)

    def write_disk(self, size: int) -> None:
        path = self.root / self.vm_config["disk"]["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x01" * size)

    def summary(self):
        with mock.patch.object(shutil, "which", return_value=None):
            return self.vmctl.vmstate.summary(self.vm_name, self.vm)

    # --- measured facts ---------------------------------------------------------------

    def test_missing_disk_has_no_data_and_no_label_beyond_no_disk(self):
        known = self.summary()
        self.assertFalse(known["disk_present"])
        self.assertEqual(known["label"], "no disk")
        self.assertEqual(known["host_size"], "-")

    def test_host_bytes_are_allocated_blocks_not_apparent_size(self):
        path = self.root / self.vm_config["disk"]["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.truncate(4 * 1024 ** 3)  # a sparse image: 4 GiB on paper, nothing allocated
        known = self.summary()
        self.assertTrue(known["disk_present"])
        self.assertLess(known["host_bytes"], vmctl.vmstate.DATA_MIN_BYTES)
        self.assertFalse(known["has_data"])
        self.assertEqual(known["label"], "empty")

    def test_raw_capacity_needs_no_qemu_img(self):
        self.vm_config["disk"]["format"] = "raw"
        self.write_config_dir()
        vm = self.vmctl.get_vm(self.vmctl.load_config(), self.vm_name)
        self.write_disk(32 * 1024 * 1024)
        with mock.patch.object(shutil, "which", return_value=None):
            facts = self.vmctl.vmstate.disk_facts(vm)
        self.assertEqual(facts["virtual_bytes"], 32 * 1024 * 1024)

    def test_qcow2_capacity_comes_from_qemu_img_when_present(self):
        self.write_disk(32 * 1024 * 1024)
        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-img"), \
             mock.patch.object(vmctl.runtime, "image_info", return_value={"virtual-size": 20 * 1024 ** 3}):
            facts = self.vmctl.vmstate.disk_facts(self.vm)
        self.assertEqual(facts["virtual_bytes"], 20 * 1024 ** 3)

    # --- the ladder --------------------------------------------------------------------

    def test_data_without_record_is_unverified(self):
        self.write_disk(32 * 1024 * 1024)
        known = self.summary()
        self.assertEqual(known["label"], "unverified")
        self.assertEqual(known["install_state"], "none")
        self.assertIn("pre-existing", known["detail"])

    def test_unattended_start_without_completion_is_incomplete(self):
        self.write_disk(32 * 1024 * 1024)
        self.vmctl.vmstate.begin_install(self.vm_name, "bootstrap-preseed")
        known = self.summary()
        self.assertEqual(known["label"], "incomplete")
        self.assertEqual(known["install_flow"], "bootstrap-preseed")
        self.assertIn("never completed", known["detail"])

    def test_interactive_start_is_unverified_not_incomplete(self):
        self.write_disk(32 * 1024 * 1024)
        self.vmctl.vmstate.begin_install(self.vm_name, "install", interactive=True)
        known = self.summary()
        self.assertEqual(known["label"], "unverified")
        self.assertIn("by hand", known["detail"])

    def test_completion_then_verification_climb_the_ladder(self):
        self.write_disk(32 * 1024 * 1024)
        self.vmctl.vmstate.begin_install(self.vm_name, "bootstrap-alpine")
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm)
        known = self.summary()
        self.assertEqual(known["label"], "installed")
        self.assertEqual(known["install_state"], "completed")
        self.assertFalse(known["verified"])
        self.vmctl.vmstate.record_verified(self.vm_name, "post-install")
        known = self.summary()
        self.assertEqual(known["label"], "verified")
        self.assertEqual(known["verify_kind"], "post-install")
        self.assertIn("bootstrap-alpine", known["detail"])

    def test_completion_without_a_matching_start_is_still_recorded(self):
        self.write_disk(32 * 1024 * 1024)
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-windows", self.vm)
        known = self.summary()
        self.assertEqual(known["label"], "installed")
        self.assertEqual(known["install_flow"], "bootstrap-windows")

    def test_a_new_install_resets_previous_verification(self):
        self.write_disk(32 * 1024 * 1024)
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm)
        self.vmctl.vmstate.record_verified(self.vm_name, "boot-check")
        self.vmctl.vmstate.begin_install(self.vm_name, "bootstrap-alpine")
        known = self.summary()
        self.assertEqual(known["label"], "incomplete")
        self.assertFalse(known["verified"])

    def test_record_over_an_empty_disk_is_stale(self):
        # rm disk.qcow2 by hand, then `vmctl prep`: the file says installed, the disk is new.
        self.write_disk(32 * 1024 * 1024)
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm)
        self.vmctl.vmstate.record_verified(self.vm_name, "post-install")
        self.write_disk(1024)
        known = self.summary()
        self.assertEqual(known["label"], "empty")
        self.assertTrue(known["stale"])

    def test_import_origin_clears_recorded_facts(self):
        self.write_disk(32 * 1024 * 1024)
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm)
        self.vmctl.vmstate.record_verified(self.vm_name, "post-install")
        self.vmctl.vmstate.record_origin(self.vm_name, "import", "/dev/sdz")
        known = self.summary()
        self.assertEqual(known["label"], "unverified")
        self.assertEqual(known["origin_kind"], "import")
        self.assertIn("/dev/sdz", known["detail"])
        self.assertEqual(known["install_state"], "none")

    def test_origin_can_keep_the_carried_record(self):
        self.write_disk(32 * 1024 * 1024)
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm)
        self.vmctl.vmstate.record_verified(self.vm_name, "post-install")
        self.vmctl.vmstate.record_origin(self.vm_name, "restore", "clean-install", keep_facts=True)
        known = self.summary()
        self.assertEqual(known["label"], "verified")
        self.assertEqual(known["origin_kind"], "restore")

    # --- file handling ------------------------------------------------------------------

    def test_dry_run_writes_nothing(self):
        self.vmctl.vmstate.begin_install(self.vm_name, "bootstrap-alpine", dry_run=True)
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm, dry_run=True)
        self.vmctl.vmstate.record_verified(self.vm_name, "post-install", dry_run=True)
        self.vmctl.vmstate.record_origin(self.vm_name, "import", "/dev/sdz", dry_run=True)
        self.assertFalse(self.vmctl.vmstate.state_path(self.vm_name).exists())

    def test_unreadable_record_reads_as_none(self):
        path = self.vmctl.vmstate.state_path(self.vm_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.vmctl.vmstate.load(self.vm_name), {})
        self.write_disk(32 * 1024 * 1024)
        self.assertEqual(self.summary()["label"], "unverified")

    def test_save_is_atomic_and_versioned(self):
        self.vmctl.vmstate.begin_install(self.vm_name, "bootstrap-alpine")
        path = self.vmctl.vmstate.state_path(self.vm_name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], vmctl.vmstate.STATE_VERSION)
        self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_copy_record_carries_or_clears(self):
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm)
        self.vmctl.vmstate.copy_record(self.vm_name, "othervm")
        self.assertEqual(self.vmctl.vmstate.load("othervm")["install"]["flow"], "bootstrap-alpine")
        self.vmctl.vmstate.copy_record("nothing-here", "othervm")
        self.assertEqual(self.vmctl.vmstate.load("othervm"), {})

    def test_compact_size(self):
        self.assertEqual(vmctl.vmstate.compact_size(None), "?")
        self.assertEqual(vmctl.vmstate.compact_size(512), "512B")
        self.assertEqual(vmctl.vmstate.compact_size(24 * 1024 * 1024), "24M")
        self.assertEqual(vmctl.vmstate.compact_size(int(8.3 * 1024 ** 3)), "8.3G")
        # never wider than four characters before the unit (the dashboard column is 16 wide)
        self.assertEqual(vmctl.vmstate.compact_size(int(427.6 * 1024 ** 2)), "428M")
        self.assertEqual(vmctl.vmstate.compact_size(int(1003.5 * 1024 ** 2)), "1G")
        self.assertEqual(vmctl.vmstate.compact_size(int(22.9 * 1024 ** 3)), "22.9G")


class VmstateHookTests(BaseVmctlTestCase):
    """The flows write the record; clean and status read it."""

    def setUp(self):
        super().setUp()
        self.write_config_dir()

    def test_clean_removes_the_record_with_the_disk(self):
        self.create_disk()
        self.vmctl.vmstate.begin_install(self.vm_name, "bootstrap-alpine")
        vm = self.vmctl.get_vm(self.vmctl.load_config(), self.vm_name)
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.vmctl.clean_vm(self.vm_name, vm)
        self.assertFalse(self.vmctl.vmstate.state_path(self.vm_name).exists())

    def test_status_shows_install_column_and_json_facts(self):
        path = self.root / self.vm_config["disk"]["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x01" * (32 * 1024 * 1024))
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine")
        self.vmctl.vmstate.record_verified(self.vm_name, "boot-check", "login:")

        with mock.patch.object(shutil, "which", return_value=None), \
             mock.patch.object(vmctl.lifecycle, "vm_runtime_status", return_value=("-", "-")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.vmctl.cmd_status(argparse.Namespace(all=False))
        self.assertIn("verified", stdout.getvalue())

        with mock.patch.object(shutil, "which", return_value=None), \
             mock.patch.object(vmctl.lifecycle, "vm_runtime_status", return_value=("-", "-")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.vmctl.cmd_status(argparse.Namespace(all=False, json=True))
        row = json.loads(stdout.getvalue())[0]
        self.assertEqual(row["install"], "verified")
        self.assertEqual(row["verify_kind"], "boot-check")
        self.assertEqual(row["install_flow"], "bootstrap-alpine")
        self.assertEqual(row["host_bytes"], path.stat().st_blocks * 512)
        self.assertNotIn("actual_size", row)

    def test_boot_check_on_the_disk_records_a_verification_but_a_cdrom_check_does_not(self):
        self.create_disk()
        base_ci = {"expect": "login:", "timeout_sec": 5, "headless": True}
        for boot_from, expected in (("disk", True), ("cdrom", False)):
            with self.subTest(boot_from=boot_from):
                self.vmctl.vmstate.forget(self.vm_name)
                self.vm_config["ci"] = dict(base_ci, boot_from=boot_from)
                self.write_config_dir()
                with mock.patch.object(vmctl.lifecycle.qemu, "common_args", return_value=["qemu-system-x86_64"]), \
                     mock.patch.object(vmctl.lifecycle.qemu, "run_and_expect"), \
                     mock.patch.object(vmctl.lifecycle.iso, "ensure_iso", return_value=self.root / "isos/test.iso"), \
                     mock.patch("sys.stdout", new_callable=io.StringIO):
                    self.vmctl.cmd_boot_check(argparse.Namespace(vm=self.vm_name, expect=None, timeout=None, dry_run=False))
                self.assertEqual(bool(self.vmctl.vmstate.load(self.vm_name).get("verify")), expected)

    def test_post_install_records_a_verification(self):
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2299, "post_install_run": ["true"]}
        self.write_config_dir()
        vm = self.vmctl.get_vm(self.vmctl.load_config(), self.vm_name)
        with mock.patch.object(vmctl.lifecycle.runtime, "require_command"), \
             mock.patch.object(vmctl.lifecycle.ssh, "wait_for_ssh"), \
             mock.patch.object(vmctl.lifecycle.ssh, "ensure_passwordless_sudo"), \
             mock.patch.object(vmctl.lifecycle.ssh, "wait_for_guest_post_install_ready"), \
             mock.patch.object(vmctl.lifecycle.ssh, "provision_shared_dir"), \
             mock.patch.object(vmctl.lifecycle.netlab, "provision_guest"), \
             mock.patch.object(vmctl.lifecycle.ssh, "post_install_run"), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.vmctl.run_post_install(self.vm_name, vm, 10)
        self.assertEqual(self.vmctl.vmstate.load(self.vm_name)["verify"]["kind"], "post-install")

    def test_failed_post_install_records_nothing(self):
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2299, "post_install_run": ["false"]}
        self.write_config_dir()
        vm = self.vmctl.get_vm(self.vmctl.load_config(), self.vm_name)
        with mock.patch.object(vmctl.lifecycle.runtime, "require_command"), \
             mock.patch.object(vmctl.lifecycle.ssh, "wait_for_ssh", side_effect=self.vmctl.VMError("no ssh")), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.run_post_install(self.vm_name, vm, 10)
        self.assertFalse(self.vmctl.vmstate.state_path(self.vm_name).exists())
