"""vmctl checkpoint: named full copies of a stopped VM's disk and EFI vars."""

import argparse
import io
import json
import os
import shutil
from pathlib import Path
from unittest import mock

import vmctl.checkpoint
import vmctl.lifecycle
import vmctl.runtime
import vmctl.vmstate
from _common import BaseVmctlTestCase


def fake_convert(cmd, dry_run=False, **kwargs):
    """qemu-img convert stand-in: copies the source file to the destination."""
    if dry_run or cmd[:2] != ["qemu-img", "convert"]:
        return
    src, dst = Path(cmd[-2]), Path(cmd[-1])
    shutil.copyfile(src, dst)


class CheckpointTestCase(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.write_config_dir()
        self.vm = self.vmctl.get_vm(self.vmctl.load_config(), self.vm_name)
        self.disk = self.root / self.vm_config["disk"]["path"]
        self.vars = self.root / self.vm_config["firmware"]["vars_path"]
        self.disk.parent.mkdir(parents=True, exist_ok=True)
        self.disk.write_bytes(b"\x01" * (32 * 1024 * 1024))
        self.vars.write_bytes(b"VARS-1")
        self.patches = [
            mock.patch.object(vmctl.runtime, "run", side_effect=fake_convert),
            mock.patch.object(vmctl.runtime, "require_command"),
            mock.patch.object(shutil, "which", return_value=None),
            mock.patch.object(vmctl.lifecycle, "vm_runtime_status", return_value=("-", "-")),
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def args(self, action, name=None, **extra):
        base = {"vm": self.vm_name, "action": action, "name": name, "dry_run": False, "yes": True,
                "note": None, "compress": False, "replace": False, "json": False}
        base.update(extra)
        return argparse.Namespace(**base)

    def checkpoint(self, name="clean-install", **extra):
        return self.vmctl.cmd_checkpoint(self.args("create", name, **extra))


class CreateTests(CheckpointTestCase):
    def test_create_copies_disk_vars_record_and_manifest(self):
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm)
        self.vmctl.vmstate.record_verified(self.vm_name, "post-install")
        self.assertEqual(self.checkpoint(note="fresh"), 0)
        target = self.vmctl.checkpoint.checkpoint_dir(self.vm_name, "clean-install")
        self.assertEqual((target / "disk.qcow2").read_bytes(), self.disk.read_bytes())
        self.assertEqual((target / "nvram.fd").read_bytes(), b"VARS-1")
        self.assertTrue((target / "state.json").is_file())
        manifest = json.loads((target / "manifest.json").read_text())
        self.assertEqual(manifest["note"], "fresh")
        self.assertEqual(manifest["disk"]["format"], "qcow2")
        self.assertEqual(manifest["nvram_file"], "nvram.fd")
        self.assertFalse(any(p.name.startswith(".") for p in target.parent.iterdir()), "no staging left behind")
        # the source is untouched
        self.assertEqual(self.disk.read_bytes(), b"\x01" * (32 * 1024 * 1024))

    def test_list_reports_what_the_checkpoint_knew(self):
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm)
        self.vmctl.vmstate.record_verified(self.vm_name, "post-install")
        with mock.patch.object(vmctl.vmstate, "now", return_value="2026-09-20T10:00:00+00:00"):
            self.checkpoint("verified-one", note="ok")
        self.vmctl.vmstate.forget(self.vm_name)
        with mock.patch.object(vmctl.vmstate, "now", return_value="2026-09-20T10:05:00+00:00"):
            self.checkpoint("unknown-one")
        rows = self.vmctl.checkpoint.list_checkpoints(self.vm_name)
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["verified-one"]["label"], "verified")
        self.assertEqual(by_name["unknown-one"]["label"], "unverified")
        self.assertTrue(by_name["verified-one"]["nvram"])
        self.assertGreater(by_name["verified-one"]["host_bytes"], 0)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.vmctl.cmd_checkpoint(self.args("list", json=True))
        self.assertEqual([row["name"] for row in json.loads(stdout.getvalue())], ["verified-one", "unknown-one"])

    def test_bios_profile_saves_no_nvram(self):
        self.vm_config["firmware"] = {"type": "bios"}
        self.write_config_dir()
        self.checkpoint()
        manifest = json.loads((self.vmctl.checkpoint.checkpoint_dir(self.vm_name, "clean-install") / "manifest.json").read_text())
        self.assertIsNone(manifest["nvram_file"])

    def test_existing_checkpoint_is_not_overwritten_without_replace(self):
        self.checkpoint()
        self.disk.write_bytes(b"\x02" * (32 * 1024 * 1024))
        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.checkpoint()
        self.assertIn("--replace", str(ctx.exception))
        target = self.vmctl.checkpoint.checkpoint_dir(self.vm_name, "clean-install")
        self.assertEqual((target / "disk.qcow2").read_bytes()[:1], b"\x01")
        self.checkpoint(replace=True)
        self.assertEqual((target / "disk.qcow2").read_bytes()[:1], b"\x02")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["clean-install"])

    def test_invalid_names_are_refused_before_anything_is_written(self):
        for bad in ("", ".hidden", "../up", "a/b", "x" * 65, "-dash", "spa ce"):
            with self.subTest(name=bad):
                with self.assertRaises(self.vmctl.VMError):
                    self.checkpoint(bad)
        self.assertFalse(self.vmctl.checkpoint.checkpoints_dir(self.vm_name).exists())

    def test_running_vm_installation_and_libvirt_are_refused(self):
        with mock.patch.object(vmctl.lifecycle, "vm_runtime_status", return_value=("tracked:4242", "-")):
            with self.assertRaises(self.vmctl.VMError) as ctx:
                self.checkpoint()
            self.assertIn("running", str(ctx.exception))
        with mock.patch.object(vmctl.lifecycle.tui_jobs, "status", return_value="running"):
            with self.assertRaises(self.vmctl.VMError) as ctx:
                self.checkpoint()
            self.assertIn("is running for it", str(ctx.exception))
        # The web page runs "Checkpoint now" as the VM's own job: that job is not in the way.
        directory = vmctl.lifecycle.tui_jobs.job_dir(vmctl.state.ROOT, self.vm_name)
        with mock.patch.object(vmctl.lifecycle.tui_jobs, "status", return_value="running"), \
             mock.patch.dict("os.environ", {vmctl.lifecycle.tui_jobs.JOB_ENV: str(directory.resolve())}):
            self.checkpoint()
        self.assertTrue(self.vmctl.checkpoint.checkpoints_dir(self.vm_name).exists())
        import shutil
        shutil.rmtree(self.vmctl.checkpoint.checkpoints_dir(self.vm_name))
        with mock.patch.object(vmctl.lifecycle, "libvirt_domain_defined", return_value=True):
            with self.assertRaises(self.vmctl.VMError) as ctx:
                self.checkpoint()
            self.assertIn("libvirt", str(ctx.exception))
        self.assertFalse(self.vmctl.checkpoint.checkpoints_dir(self.vm_name).exists())

    def test_libvirt_detection_uses_virsh_list_and_tolerates_a_missing_virsh(self):
        self.assertFalse(self.vmctl.libvirt_domain_defined(self.vm_name))  # which() -> None
        with mock.patch.object(shutil, "which", return_value="/usr/bin/virsh"), \
             mock.patch.object(vmctl.lifecycle.libvirt, "virsh_output", return_value="other\ntestvm\n"):
            self.assertTrue(self.vmctl.libvirt_domain_defined(self.vm_name))
        with mock.patch.object(shutil, "which", return_value="/usr/bin/virsh"), \
             mock.patch.object(vmctl.lifecycle.libvirt, "virsh_output", return_value="other\n"):
            self.assertFalse(self.vmctl.libvirt_domain_defined(self.vm_name))

    def test_tpm_profile_is_refused(self):
        self.vm_config["tpm"] = True
        self.write_config_dir()
        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.checkpoint()
        self.assertIn("TPM", str(ctx.exception))

    def test_missing_disk_is_refused(self):
        self.disk.unlink()
        with self.assertRaises(self.vmctl.VMError):
            self.checkpoint()

    def test_failed_conversion_leaves_no_checkpoint(self):
        def boom(cmd, dry_run=False, **kwargs):
            raise self.vmctl.VMError("convert failed")
        with mock.patch.object(vmctl.runtime, "run", side_effect=boom):
            with self.assertRaises(self.vmctl.VMError):
                self.checkpoint()
        base = self.vmctl.checkpoint.checkpoints_dir(self.vm_name)
        self.assertFalse(base.exists() and any(base.iterdir()))

    def test_dry_run_writes_nothing(self):
        self.checkpoint(dry_run=True)
        self.assertFalse(self.vmctl.checkpoint.checkpoints_dir(self.vm_name).exists())

    def test_compress_adds_the_flag_for_qcow2_only(self):
        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_convert) as run:
            self.checkpoint("c1", compress=True)
        self.assertIn("-c", run.call_args_list[0].args[0])
        self.vm_config["disk"]["format"] = "raw"
        self.write_config_dir()
        with mock.patch.object(vmctl.runtime, "run", side_effect=fake_convert) as run:
            self.checkpoint("c2", compress=True)
        self.assertNotIn("-c", run.call_args_list[0].args[0])
        self.assertTrue((self.vmctl.checkpoint.checkpoint_dir(self.vm_name, "c2") / "disk.img").is_file())


class RestoreDeleteTests(CheckpointTestCase):
    def test_restore_swaps_disk_vars_and_record_and_notes_the_origin(self):
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm)
        self.vmctl.vmstate.record_verified(self.vm_name, "post-install")
        self.checkpoint()
        self.disk.write_bytes(b"\x02" * (32 * 1024 * 1024))
        self.vars.write_bytes(b"VARS-2")
        self.vmctl.vmstate.begin_install(self.vm_name, "bootstrap-alpine")  # now "incomplete"

        self.assertEqual(self.vmctl.cmd_checkpoint(self.args("restore", "clean-install")), 0)
        self.assertEqual(self.disk.read_bytes()[:1], b"\x01")
        self.assertEqual(self.vars.read_bytes(), b"VARS-1")
        known = self.vmctl.vmstate.summary(self.vm_name, self.vm)
        self.assertEqual(known["label"], "verified")
        self.assertEqual(known["origin_kind"], "restore")
        self.assertEqual(known["origin_source"], "clean-install")
        self.assertFalse(any(p.name.startswith(".restore-") for p in self.disk.parent.iterdir()))
        # the checkpoint itself is still there, usable again
        self.assertTrue((self.vmctl.checkpoint.checkpoint_dir(self.vm_name, "clean-install") / "disk.qcow2").is_file())

    def test_restore_without_a_record_leaves_the_disk_unverified(self):
        self.vmctl.vmstate.forget(self.vm_name)
        self.checkpoint()
        self.vmctl.vmstate.complete_install(self.vm_name, "bootstrap-alpine", self.vm)
        self.vmctl.cmd_checkpoint(self.args("restore", "clean-install"))
        known = self.vmctl.vmstate.summary(self.vm_name, self.vm)
        self.assertEqual(known["label"], "unverified")
        self.assertEqual(known["origin_kind"], "restore")

    def test_restore_onto_a_cleaned_vm_recreates_the_disk(self):
        self.checkpoint()
        with mock.patch.object(vmctl.lifecycle, "cmd_stop"):
            self.vmctl.cmd_clean(argparse.Namespace(vm=self.vm_name, all=False, dry_run=False, checkpoints=False))
        self.assertFalse(self.disk.exists())
        self.assertTrue(self.vmctl.checkpoint.checkpoint_dir(self.vm_name, "clean-install").is_dir(), "clean keeps checkpoints")
        self.vmctl.cmd_checkpoint(self.args("restore", "clean-install"))
        self.assertEqual(self.disk.read_bytes()[:1], b"\x01")
        self.assertEqual(self.vars.read_bytes(), b"VARS-1")

    def test_clean_with_checkpoints_flag_removes_them(self):
        self.checkpoint()
        with mock.patch.object(vmctl.lifecycle, "cmd_stop"):
            self.vmctl.cmd_clean(argparse.Namespace(vm=self.vm_name, all=False, dry_run=False, checkpoints=True))
        self.assertFalse(self.vmctl.checkpoint.checkpoints_dir(self.vm_name).exists())

    def test_failed_conversion_during_restore_keeps_the_current_disk(self):
        self.checkpoint()
        self.disk.write_bytes(b"\x02" * (32 * 1024 * 1024))

        def boom(cmd, dry_run=False, **kwargs):
            raise self.vmctl.VMError("convert failed")
        with mock.patch.object(vmctl.runtime, "run", side_effect=boom):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_checkpoint(self.args("restore", "clean-install"))
        self.assertEqual(self.disk.read_bytes()[:1], b"\x02")
        self.assertEqual(self.vars.read_bytes(), b"VARS-1")
        self.assertFalse(any(p.name.startswith(".restore-") for p in self.disk.parent.iterdir()))

    def test_failed_swap_puts_the_previous_files_back(self):
        self.checkpoint()
        self.disk.write_bytes(b"\x02" * (32 * 1024 * 1024))
        self.vars.write_bytes(b"VARS-2")
        real_rename = os.rename
        calls = {"n": 0}

        def flaky_rename(src, dst):
            # the disk swap (park current, place staged) succeeds; the vars swap fails
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("simulated rename failure")
            real_rename(src, dst)
        with mock.patch.object(vmctl.checkpoint.os, "rename", side_effect=flaky_rename):
            with self.assertRaises(self.vmctl.VMError) as ctx:
                self.vmctl.cmd_checkpoint(self.args("restore", "clean-install"))
        self.assertIn("previous files were put back", str(ctx.exception))
        self.assertEqual(self.disk.read_bytes()[:1], b"\x02")
        self.assertEqual(self.vars.read_bytes(), b"VARS-2")

    def test_restore_and_delete_need_confirmation_unless_yes(self):
        self.checkpoint()
        with mock.patch.object(vmctl.runtime, "confirm_default_no", return_value=False):
            with self.assertRaises(self.vmctl.VMError) as ctx:
                self.vmctl.cmd_checkpoint(self.args("restore", "clean-install", yes=False))
            self.assertIn("--yes", str(ctx.exception))
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_checkpoint(self.args("delete", "clean-install", yes=False))
        self.assertTrue(self.vmctl.checkpoint.checkpoint_dir(self.vm_name, "clean-install").is_dir())
        with mock.patch.object(vmctl.runtime, "confirm_default_no", return_value=True):
            self.vmctl.cmd_checkpoint(self.args("delete", "clean-install", yes=False))
        self.assertFalse(self.vmctl.checkpoint.checkpoints_dir(self.vm_name).exists())

    def test_restore_refuses_running_vm_and_unknown_checkpoint(self):
        self.checkpoint()
        with mock.patch.object(vmctl.lifecycle, "vm_runtime_status", return_value=("hostfwd:2222", "pid=1")):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_checkpoint(self.args("restore", "clean-install"))
        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_checkpoint(self.args("restore", "nope"))
        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_checkpoint(self.args("delete", "nope"))

    def test_dry_run_restore_and_delete_change_nothing(self):
        self.checkpoint()
        self.disk.write_bytes(b"\x02" * (32 * 1024 * 1024))
        self.vmctl.cmd_checkpoint(self.args("restore", "clean-install", dry_run=True, yes=False))
        self.vmctl.cmd_checkpoint(self.args("delete", "clean-install", dry_run=True, yes=False))
        self.assertEqual(self.disk.read_bytes()[:1], b"\x02")
        self.assertTrue(self.vmctl.checkpoint.checkpoint_dir(self.vm_name, "clean-install").is_dir())
        self.assertFalse(any(p.name.startswith(".") for p in self.disk.parent.iterdir()))

    def test_incomplete_checkpoint_is_hidden_from_list_and_refused_by_restore(self):
        self.checkpoint()
        target = self.vmctl.checkpoint.checkpoint_dir(self.vm_name, "clean-install")
        (target / "disk.qcow2").unlink()
        self.assertEqual(self.vmctl.checkpoint.list_checkpoints(self.vm_name)[0]["complete"], False)
        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.cmd_checkpoint(self.args("restore", "clean-install"))
        self.assertIn("incomplete", str(ctx.exception))
        (target / "manifest.json").unlink()
        self.assertEqual(self.vmctl.checkpoint.list_checkpoints(self.vm_name), [])


class CheckpointCliTests(CheckpointTestCase):
    def test_checkpoint_is_a_registered_grouped_command(self):
        import vmctl.cli
        groups = {name for _, _, names in vmctl.cli.COMMAND_GROUPS for name in names}
        self.assertIn("checkpoint", groups)
        parser = vmctl.cli.build_parser()
        args = parser.parse_args(["checkpoint", "create", "testvm", "clean-install", "--note", "x", "--compress"])
        self.assertEqual((args.action, args.vm, args.name, args.note, args.compress), ("create", "testvm", "clean-install", "x", True))
        args = parser.parse_args(["clean", "testvm", "--checkpoints"])
        self.assertTrue(args.checkpoints)
