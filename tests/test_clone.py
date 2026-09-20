"""vmctl clone: an independent copy of a stopped VM as a new local profile."""

import argparse
import io
import json
import shutil
from pathlib import Path
from unittest import mock

import vmctl.clone
import vmctl.lifecycle
import vmctl.runtime
import vmctl.vmstate
from _common import BaseVmctlTestCase
from test_checkpoint import fake_convert


class CloneTestCase(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm_config["firmware"] = {
            "type": "efi",
            "code": "firmware/OVMF_CODE_4M.fd",
            "vars_template": "firmware/OVMF_VARS_4M.fd",
            "vars_path": "artifacts/testvm/OVMF_VARS.fd",
        }
        self.vm_config["ssh_provision"] = {"user": "lab", "hostname": "testbox", "ssh_host_port": 2299,
                                           "post_install_run": ["true"]}
        self.vm_config["networks"] = [{"type": "user", "mac": "52:54:00:aa:bb:cc",
                                       "hostfwd": [{"host_port": 8080, "guest_port": 80}]}]
        self.vm_config["meta"] = {"status": "unattended", "verified": "2026-09-01"}
        self.write_config_dir()
        self.disk = self.root / "artifacts/testvm/disk.qcow2"
        self.vars = self.root / "artifacts/testvm/OVMF_VARS.fd"
        self.disk.parent.mkdir(parents=True, exist_ok=True)
        self.disk.write_bytes(b"\x01" * (32 * 1024 * 1024))
        self.vars.write_bytes(b"VARS-1")
        key_dir = self.disk.parent / "ssh"
        key_dir.mkdir()
        (key_dir / "id_ed25519").write_text("private\n")
        (key_dir / "id_ed25519.pub").write_text("public\n")
        (self.disk.parent / "runtime").mkdir()
        (self.disk.parent / "runtime" / "stale.pid").write_text("1\n")
        (self.disk.parent / "logs").mkdir()
        (self.disk.parent / "logs" / "install.log").write_text("log\n")
        self.vmctl.vmstate.complete_install("testvm", "bootstrap-alpine")
        self.vmctl.vmstate.record_verified("testvm", "post-install")
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
        self.cfg = self.vmctl.load_config()
        self.vm = self.vmctl.get_vm(self.cfg, "testvm")

    def args(self, destination="testvm-2", **extra):
        base = {"vm": "testvm", "destination": destination, "dry_run": False, "identity": "keep",
                "ssh_port": None, "timeout": 5}
        base.update(extra)
        return argparse.Namespace(**base)

    def local_json(self):
        path = self.config_dir / "profiles" / "local.json"
        return json.loads(path.read_text()) if path.is_file() else None


class DeriveProfileTests(CloneTestCase):
    def test_paths_ports_macs_and_meta_are_rewritten(self):
        profile, report = self.vmctl.clone.derive_profile(self.cfg, "testvm", self.vm, "testvm-2")
        self.assertEqual(profile["disk"]["path"], "artifacts/testvm-2/disk.qcow2")
        self.assertEqual(profile["firmware"]["vars_path"], "artifacts/testvm-2/OVMF_VARS.fd")
        self.assertEqual(profile["name"], "Test VM (clone of testvm)")
        self.assertEqual(profile["meta"]["clone_of"], "testvm")
        self.assertNotIn("verified", profile["meta"])
        self.assertNotEqual(profile["ssh_provision"]["ssh_host_port"], 2299)
        self.assertNotEqual(profile["networks"][0]["hostfwd"][0]["host_port"], 8080)
        self.assertNotIn("mac", profile["networks"][0])
        self.assertEqual(report["macs_dropped"], 1)
        self.assertEqual(set(report["ports"]), {2299, 8080})
        self.assertEqual(len(set(report["ports"].values())), 2)
        # the origin is untouched
        self.assertEqual(self.vm["ssh_provision"]["ssh_host_port"], 2299)
        self.assertEqual(self.vm["networks"][0]["mac"], "52:54:00:aa:bb:cc")
        # the default MAC follows the disk path, so the clone's differs by itself
        self.assertNotEqual(self.vmctl.default_nic_mac(profile, 0), self.vmctl.default_nic_mac(self.vm, 0))

    def test_allocated_ports_avoid_every_profile_and_each_other(self):
        taken = self.vmctl.clone.used_host_ports(self.cfg)
        self.assertIn(2299, taken)
        self.assertIn(8080, taken)
        ports = self.vmctl.clone.allocate_ports(3, taken | {2300, 2302})
        self.assertEqual(ports, [2301, 2303, 2304])
        self.assertEqual(self.vmctl.clone.allocate_ports(2, taken, preferred=2400), [2400, 2300])
        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.clone.allocate_ports(1, taken, preferred=2299)

    def test_ssh_port_option_lands_in_the_profile(self):
        profile, _ = self.vmctl.clone.derive_profile(self.cfg, "testvm", self.vm, "testvm-2", ssh_port=2400)
        self.assertEqual(profile["ssh_provision"]["ssh_host_port"], 2400)

    def test_destination_and_source_checks(self):
        for bad in ("", "Bad", "a b", "-x", "../y", "testvm", "archlinux"):
            with self.subTest(name=bad):
                with self.assertRaises(self.vmctl.VMError):
                    self.vmctl.clone.check_destination(self.cfg, bad)
        base = self.root / "artifacts/testvm-2"
        base.mkdir(parents=True)
        (base / "leftover").write_text("x")
        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.clone.check_destination(self.cfg, "testvm-2")
        self.assertIsNone(self.vmctl.clone.check_source("testvm", self.vm))
        lab_vm = dict(self.vm, network_lab={"role": "client"})
        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.clone.check_source("testvm", lab_vm)
        self.assertIn("network lab", str(ctx.exception))
        self.disk.unlink()
        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.clone.check_source("testvm", self.vm)

    def test_identity_support_and_script(self):
        self.assertEqual(self.vmctl.clone.identity_support(self.vm), (True, ""))
        for section in ("windows_config", "pfsense_config", "nixos_config", "freebsd_config", "reactos_config"):
            supported, advice = self.vmctl.clone.identity_support(dict(self.vm, **{section: {}}))
            self.assertFalse(supported, section)
            self.assertTrue(advice)
        no_ssh = {k: v for k, v in self.vm.items() if k != "ssh_provision"}
        self.assertFalse(self.vmctl.clone.identity_support(no_ssh)[0])
        script = self.vmctl.clone.identity_script("clone-2", "testbox")
        for needle in ("hostnamectl set-hostname", "/etc/machine-id", "ssh-keygen -A", "NEW=clone-2", "OLD=testbox", "set -e"):
            self.assertIn(needle, script)
        self.assertEqual(self.vmctl.clone.guest_hostname(self.vm), "testbox")


class CloneCommandTests(CloneTestCase):
    def test_clone_copies_the_right_files_and_publishes_last(self):
        self.assertEqual(self.vmctl.cmd_clone(self.args()), 0)
        base = self.root / "artifacts/testvm-2"
        self.assertEqual((base / "disk.qcow2").read_bytes(), self.disk.read_bytes())
        self.assertEqual((base / "OVMF_VARS.fd").read_bytes(), b"VARS-1")
        self.assertEqual((base / "ssh" / "id_ed25519").read_text(), "private\n")
        self.assertFalse((base / "runtime" / "stale.pid").exists())
        self.assertFalse((base / "logs" / "install.log").exists())
        self.assertFalse(any(p.name.startswith(".clone-") for p in base.parent.iterdir()))
        known = self.vmctl.vmstate.summary("testvm-2", self.vmctl.get_vm(self.vmctl.load_config(), "testvm-2"))
        self.assertEqual(known["label"], "verified")
        self.assertEqual(known["origin_kind"], "clone")
        self.assertEqual(known["origin_source"], "testvm")
        local = self.local_json()
        self.assertIn("testvm-2", local["vms"])
        self.assertEqual(local["vms"]["testvm-2"]["disk"]["path"], "artifacts/testvm-2/disk.qcow2")
        # the tracked profile file is untouched and the origin still loads unchanged
        self.assertEqual(json.loads((self.config_dir / "profiles" / "test.json").read_text())["vms"]["testvm"]["ssh_provision"]["ssh_host_port"], 2299)
        cfg = self.vmctl.load_config()  # both profiles validate together: ports are distinct
        self.assertEqual(self.vmctl.get_vm(cfg, "testvm")["ssh_provision"]["ssh_host_port"], 2299)

    def test_existing_local_json_is_merged_and_backed_up(self):
        local = self.config_dir / "profiles" / "local.json"
        local.write_text(json.dumps({"vms": {"testvm": {"memory_mb": 4096}}}) + "\n")
        self.vmctl.cmd_clone(self.args())
        payload = self.local_json()
        self.assertEqual(payload["vms"]["testvm"], {"memory_mb": 4096})
        self.assertEqual(payload["vms"]["testvm-2"]["memory_mb"], 4096)  # the clone copies the resolved origin
        self.assertTrue(local.with_suffix(".json.bak").is_file())

    def test_failed_disk_copy_leaves_origin_intact_and_publishes_nothing(self):
        def boom(cmd, dry_run=False, **kwargs):
            raise self.vmctl.VMError("convert failed")
        with mock.patch.object(vmctl.runtime, "run", side_effect=boom):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_clone(self.args())
        self.assertFalse((self.root / "artifacts/testvm-2").exists())
        self.assertFalse(any(p.name.startswith(".clone-") for p in (self.root / "artifacts").iterdir()))
        self.assertIsNone(self.local_json())
        self.assertEqual(self.disk.read_bytes()[:1], b"\x01")
        self.assertTrue(self.vmctl.vmstate.state_path("testvm").is_file())

    def test_running_installing_libvirt_and_existing_names_are_refused(self):
        with mock.patch.object(vmctl.lifecycle, "vm_runtime_status", return_value=("running:7", "-")):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_clone(self.args())
        with mock.patch.object(vmctl.lifecycle.tui_jobs, "status", return_value="running"):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_clone(self.args())
        with mock.patch.object(vmctl.lifecycle, "libvirt_domain_defined", return_value=True):
            with self.assertRaises(self.vmctl.VMError):
                self.vmctl.cmd_clone(self.args())
        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.cmd_clone(self.args(destination="testvm"))
        self.assertIsNone(self.local_json())
        self.assertFalse((self.root / "artifacts/testvm-2").exists())

    def test_dry_run_writes_nothing(self):
        self.vmctl.cmd_clone(self.args(dry_run=True, identity="regenerate"))
        self.assertIsNone(self.local_json())
        self.assertFalse((self.root / "artifacts/testvm-2").exists())

    def test_regenerate_runs_the_script_as_root_and_stops_the_clone(self):
        calls = []

        def run(cmd, dry_run=False, **kwargs):
            calls.append(cmd)
            fake_convert(cmd, dry_run=dry_run, **kwargs)
        with mock.patch.object(vmctl.runtime, "run", side_effect=run), \
             mock.patch.object(vmctl.lifecycle, "start_installed_vm_headless") as start, \
             mock.patch.object(vmctl.lifecycle.ssh, "wait_for_ssh"), \
             mock.patch.object(vmctl.lifecycle.ssh, "ensure_passwordless_sudo"), \
             mock.patch.object(vmctl.lifecycle, "cmd_stop") as stop:
            self.assertEqual(self.vmctl.cmd_clone(self.args(identity="regenerate")), 0)
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0], "testvm-2")
        stop.assert_called_once()
        self.assertEqual(stop.call_args.args[0].vm, "testvm-2")
        ssh_calls = [cmd for cmd in calls if cmd and cmd[0] == "ssh"]
        self.assertEqual(len(ssh_calls), 1)
        self.assertIn("-p", ssh_calls[0])
        self.assertNotIn("2299", ssh_calls[0])  # the clone's own port
        self.assertTrue(ssh_calls[0][-1].startswith("sudo sh -lc "))
        self.assertIn("hostnamectl", ssh_calls[0][-1])
        self.assertIn("testvm-2", ssh_calls[0][-1])

    def test_failed_regeneration_unpublishes_the_clone(self):
        with mock.patch.object(vmctl.lifecycle, "start_installed_vm_headless"), \
             mock.patch.object(vmctl.lifecycle.ssh, "wait_for_ssh", side_effect=self.vmctl.VMError("no ssh")), \
             mock.patch.object(vmctl.lifecycle, "cmd_stop"):
            with self.assertRaises(self.vmctl.VMError) as ctx:
                self.vmctl.cmd_clone(self.args(identity="regenerate"))
        self.assertIn("removed again", str(ctx.exception))
        self.assertIsNone(self.local_json())
        self.assertFalse((self.root / "artifacts/testvm-2").exists())
        self.assertEqual(self.disk.read_bytes()[:1], b"\x01")

    def test_regenerate_is_refused_for_unsupported_guests_before_copying(self):
        self.vm_config["windows_config"] = {"username": "lab", "password": "x", "edition": "Windows 11 Pro"}
        self.write_config_dir()
        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.cmd_clone(self.args(identity="regenerate"))
        self.assertIn("sysprep", str(ctx.exception))
        self.assertFalse((self.root / "artifacts/testvm-2").exists())

    def test_clean_remove_profile_deletes_a_clone_and_refuses_tracked_profiles(self):
        self.vmctl.cmd_clone(self.args())
        local = self.config_dir / "profiles" / "local.json"
        payload = json.loads(local.read_text())
        payload["vms"]["testvm"] = {"memory_mb": 4096}  # an override of the tracked origin, must survive
        local.write_text(json.dumps(payload) + "\n")
        with mock.patch.object(vmctl.lifecycle, "cmd_stop"):
            with self.assertRaises(self.vmctl.VMError) as ctx:
                self.vmctl.cmd_clean(argparse.Namespace(vm="testvm", all=False, dry_run=False, checkpoints=False, remove_profile=True))
            self.assertIn("tracked profile", str(ctx.exception))
            self.assertTrue(self.disk.exists())
            self.vmctl.cmd_clean(argparse.Namespace(vm="testvm-2", all=False, dry_run=True, checkpoints=False, remove_profile=True))
            self.assertIn("testvm-2", self.local_json()["vms"])
            self.assertTrue((self.root / "artifacts/testvm-2/disk.qcow2").exists())
            self.vmctl.cmd_clean(argparse.Namespace(vm="testvm-2", all=False, dry_run=False, checkpoints=False, remove_profile=True))
        self.assertEqual(self.local_json()["vms"], {"testvm": {"memory_mb": 4096}})
        self.assertFalse((self.root / "artifacts/testvm-2").exists())
        self.assertTrue(local.with_suffix(".json.bak").is_file())
        self.assertNotIn("testvm-2", self.vmctl.load_config()["vms"])
        with self.assertRaises(self.vmctl.VMError):
            self.vmctl.clone.delete_local_profile("nothing-here")

    def test_cli_registration(self):
        import vmctl.cli
        groups = {name for _, _, names in vmctl.cli.COMMAND_GROUPS for name in names}
        self.assertIn("clone", groups)
        args = vmctl.cli.build_parser().parse_args(["clone", "testvm", "testvm-2", "--identity", "regenerate", "--ssh-port", "2400"])
        self.assertEqual((args.vm, args.destination, args.identity, args.ssh_port), ("testvm", "testvm-2", "regenerate", 2400))
