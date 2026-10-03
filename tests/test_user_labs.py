"""Local scaffolds run entirely under a temporary ROOT, with no QEMU or real local.json."""
import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
from unittest import mock

from tests._common import ROOT, BaseVmctlTestCase, enter_context
from vmctl import catalog, cli, clone, cloudimg, config, iso, labs, lifecycle, runtime, user_labs, webui
from vmctl.errors import VMError


class UserLabsTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        for source in (ROOT / "vms/profiles").glob("*.json"):
            if source.name != "local.json":
                shutil.copy(source, self.config_dir / "profiles" / source.name)
        shutil.copytree(ROOT / "vms/labs", self.config_dir / "labs")
        enter_context(self, mock.patch.dict(os.environ, {config.TRACKED_ONLY_ENV: "0"}))
        self.output = enter_context(self, contextlib.redirect_stdout(io.StringIO()))
        self.local = self.config_dir / "profiles/local.json"
        self.directory = self.config_dir / "labs.local/demo"
        self.members = ["server=ubuntu-cloud-base", "client=ubuntu-cloud-base"]
        # Any accidental runtime command (QEMU, xorriso, downloads) must fail the test.
        enter_context(self, mock.patch.object(runtime, "run", side_effect=AssertionError("external command")))

    def create(self, **kwargs):
        user_labs.create("demo", self.members, **kwargs)

    def test_real_scaffold_and_every_content_consumer(self):
        self.create(title="My lab")
        cfg = config.load_config()
        content = labs.load_content("demo")
        self.assertIn("demo", labs.content_groups())
        self.assertIn("demo", labs.lab_groups(cfg))
        self.assertEqual(content["dir"], "vms/labs.local/demo")
        self.assertEqual(content["title"], "My lab")
        self.assertEqual(set(content["guides"]), {"en", "it"})
        self.assertEqual(content["tests"], ["test_01_reachability.sh", "test_02_internet.sh"])
        self.assertEqual(set(content["members"]), {"demo-server", "demo-client"})
        model = labs.model(cfg, "demo")
        self.assertEqual(model["segments"][0]["subnet"], "172.20.3.0/24")
        self.assertIn("My lab", labs.render_html(model))
        self.assertIn("My lab", webui.lab_guide_page("demo", "en").decode())
        self.assertEqual([g for g, _, _ in lifecycle.lab_test_groups(cfg, content["members"])], ["demo"])
        args = cli.build_parser().parse_args(["group", "list", "--labs", "--json"])
        out = io.StringIO()
        with mock.patch.object(lifecycle.vmlink, "session_labs", return_value=[]), contextlib.redirect_stdout(out):
            self.assertEqual(lifecycle.cmd_group(args), 0)
        # This is the payload ClassicBridge sends to the web lab cards.
        card = next(entry for entry in json.loads(out.getvalue()) if entry["group"] == "demo")
        self.assertEqual((card["title"], card["tests"]), ("My lab", 2))
        for index, name in enumerate(content["members"], 1):
            vm = cfg["vms"][name]
            self.assertEqual(vm["extends"], "ubuntu-cloud-base")
            self.assertEqual(vm["disk"]["path"], f"artifacts/{name}/disk.qcow2")
            netplan = cloudimg.segment_netplan(vm)
            self.assertIn(f"172.20.3.{index}/24", netplan[0]["content"])
            self.assertIn("user-lab-3", netplan[0]["content"])
        self.assertIn("2048 MiB RAM, 2 vCPU, 20 GiB", self.output.getvalue())
        self.assertEqual(self.local.stat().st_mode & 0o777, 0o600)

    def test_scaffold_script_checks_every_direction_using_common_helpers(self):
        self.members.append("router=ubuntu-cloud-base")
        self.create()
        # Execute the real generated script against a fake vmctl, never SSH or guests.
        binary = self.root / "bin/vmctl"
        binary.parent.mkdir()
        binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nexit "${FAKE_EXIT:-0}"\n')
        binary.chmod(0o755)
        calls = self.root / "calls.txt"
        env = {"PATH": os.defpath, "HOME": str(self.root), "LC_ALL": "C.UTF-8", "CALLS": str(calls)}
        script = self.directory / "tests/test_01_reachability.sh"
        result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("[PASS]"), 6)
        self.assertIn("shell demo-server -- ping -c 2 172.20.3.2", calls.read_text())
        result = subprocess.run(["bash", str(script)], env={**env, "FAKE_EXIT": "1"}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 6)
        self.assertEqual(result.stdout.count("[FAIL]"), 6)

    def test_dry_run_and_parser_do_not_write(self):
        args = cli.build_parser().parse_args(["group", "new", "demo", "--member", self.members[0],
                                             "--member", self.members[1], "--title", "Demo", "--dry-run"])
        self.assertEqual(lifecycle.cmd_group(args), 0)
        self.assertFalse(self.local.exists())
        self.assertFalse(self.directory.parent.exists())
        self.assertIn("Would update local.json", self.output.getvalue())
        self.assertEqual(sum(names.count("group") for _, _, names in cli.COMMAND_GROUPS), 1)

    def test_members_get_the_local_identity_explicitly(self):
        # Without an identity the entries carry none; with one, each member installs as that user
        # (the top-level identity moves tracked profiles only, a local-only member would stay "lab").
        self.create()
        self.assertNotIn("user", json.loads(self.local.read_text())["vms"]["demo-server"]["ssh_provision"])
        user_labs.remove("demo")
        document = catalog.read_document()
        document["identity"] = {"user": "probeuser", "password_hash": "$6$probe$hash", "realname": "Probe"}
        catalog.write_document(document)
        self.create()
        entry = json.loads(self.local.read_text())["vms"]["demo-server"]
        self.assertEqual(entry["ssh_provision"]["user"], "probeuser")
        self.assertEqual(entry["cloudimg_config"]["username"], "probeuser")
        self.assertEqual(entry["cloudimg_config"]["password_hash"], "$6$probe$hash")
        vm = config.load_config()["vms"]["demo-server"]
        self.assertEqual(config.resolve_vm_user(vm), ("probeuser", None))
        self.assertNotIn("{{user}}", json.dumps(vm))  # the base's placeholders follow the member's user
        self.assertNotIn("lab", json.dumps(vm["cloudimg_config"].get("username")))

    def test_cloud_internet_script_checks_each_member_and_reports_failures(self):
        self.create()
        binary = self.root / "bin/vmctl"
        binary.parent.mkdir()
        binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nexit "${FAKE_EXIT:-0}"\n')
        binary.chmod(0o755)
        calls = self.root / "calls.txt"
        env = {"PATH": os.defpath, "HOME": str(self.root), "LC_ALL": "C.UTF-8", "CALLS": str(calls)}
        script = self.directory / "tests/test_02_internet.sh"
        result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("[PASS]"), 2)
        for name in ("demo-server", "demo-client"):
            self.assertIn(f"shell {name} -- python3 -c", calls.read_text())
        self.assertIn('urlopen("https://example.org", timeout=10)', calls.read_text())
        self.assertIn("except urllib.error.HTTPError: pass", calls.read_text())  # an HTTP status is a reached server
        result = subprocess.run(["bash", str(script)], env={**env, "FAKE_EXIT": "1"}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout.count("[FAIL]"), 2)
        self.assertIn("Internet access", [item["title"] for item in labs.load_content("demo")["exercises"]])

    def test_mixed_lab_does_not_generate_cloud_internet_check(self):
        self.members[0] = "server=debian-server"
        self.create()
        content = labs.load_content("demo")
        self.assertEqual(content["tests"], ["test_01_reachability.sh"])
        self.assertNotIn("Internet access", [item["title"] for item in content["exercises"]])

    def test_backup_and_unrelated_local_values_are_preserved(self):
        before = {"vms": {}, "catalog": {"selected": ["testvm"]}, "protected": ["testvm"], "note": "private"}
        self.local.write_text(json.dumps(before))
        self.create()
        self.assertEqual(json.loads(self.local.with_suffix(".json.bak").read_text()), before)
        after = catalog.read_document()
        self.assertEqual({k: v for k, v in after.items() if k != "vms"}, {k: v for k, v in before.items() if k != "vms"})

    def test_allocations_skip_local_ports_and_networks(self):
        cfg = config.load_config()
        expected = clone.allocate_ports(3, clone.used_host_ports(cfg), start=user_labs.USER_LAB_PORT_START)
        self.local.write_text(json.dumps({"vms": {"occupied": {
            "name": "Occupied", "extends": "ubuntu-cloud-base",
            "ssh_provision": {"ssh_host_port": expected[0]},
            "networks": [{"type": "segment", "name": "occupied", "address": "172.20.3.2/24"}]}}}))
        self.create()
        cfg = config.load_config()
        self.assertEqual(cfg["vms"]["demo-server"]["ssh_provision"]["ssh_host_port"], expected[1])
        self.assertEqual(cfg["vms"]["demo-client"]["ssh_provision"]["ssh_host_port"], expected[2])
        self.assertEqual(labs.model(cfg, "demo")["segments"][0]["subnet"], "172.20.4.0/24")
        user_labs.create("another", self.members)
        self.assertEqual(labs.model(config.load_config(), "another")["segments"][0]["subnet"], "172.20.5.0/24")

    def test_local_lab_ports_stay_clear_of_the_catalog_numbering(self):
        # 2026-10-03: a tracked profile added later took 2365, the port a scaffolded lab already held.
        self.create()
        ports = sorted(json.loads(self.local.read_text())["vms"][n]["ssh_provision"]["ssh_host_port"] for n in ("demo-server", "demo-client"))
        self.assertEqual(ports, [user_labs.USER_LAB_PORT_START, user_labs.USER_LAB_PORT_START + 1])

    def test_subnet_allocator_handles_wider_prefix_and_exhaustion(self):
        cfg = {"vms": {"a": {"networks": [{"address": "172.20.1.1/23"}]}}}
        self.assertEqual(user_labs.allocate_subnet(cfg), 2)
        cfg["vms"]["a"]["networks"][0]["address"] = "172.20.0.1/16"
        with self.assertRaisesRegex(VMError, "No free lab subnet"):
            user_labs.allocate_subnet(cfg)

    def test_ports_skip_host_forwards_in_both_phases(self):
        cfg = config.load_config()
        expected = clone.allocate_ports(4, clone.used_host_ports(cfg), start=user_labs.USER_LAB_PORT_START)
        self.local.write_text(json.dumps({"vms": {"testvm": {"networks": [
            {"type": "user", "phase": "install", "hostfwd": [{"host_port": expected[0], "guest_port": 80}]},
            {"type": "user", "phase": "runtime", "hostfwd": [{"host_port": expected[1], "guest_port": 80}]}]}}}))
        self.create()
        cfg = config.load_config()
        self.assertEqual([cfg["vms"][name]["ssh_provision"]["ssh_host_port"] for name in ("demo-server", "demo-client")], expected[2:])

    def test_check_vms_records_local_lab_row(self):
        self.create()
        cfg = config.load_config()
        members = labs.group_members(cfg, "demo")
        results = [(name, "passed", "installed") for name in members]
        with mock.patch.object(lifecycle, "group_states", return_value={name: {"running": False} for name in members}), \
             mock.patch.object(lifecycle, "cmd_start") as start, mock.patch.object(lifecycle, "cmd_stop") as stop, \
             mock.patch.object(lifecycle.ssh, "wait_for_ssh"), mock.patch.object(lifecycle.report, "record_group_row"), \
             mock.patch.object(labs, "run_lab_tests", return_value=[{"script": "test_01_reachability.sh", "status": "passed", "passed": 2, "failed": 0}]) as run:
            lifecycle.run_lab_test_rows(cfg, members, results, argparse.Namespace(dry_run=False, timeout=60, _report_dir=None))
        self.assertEqual(results[-1][:2], ("lab-demo", "passed"))
        self.assertEqual(run.call_args.args[0]["dir"], "vms/labs.local/demo")
        self.assertEqual(start.call_count, 2)
        self.assertEqual(stop.call_count, 2)

    def test_profile_source_gets_private_base_and_artifacts(self):
        self.members = ["server=lvm-lab-server", "client=ubuntu-24.04-cloud"]
        self.create()
        cfg = config.load_config()
        server = cfg["vms"]["demo-server"]
        self.assertEqual(server["extends"], "demo-server-base")
        self.assertEqual(config.declared_groups(server), ["demo"])
        self.assertNotIn("version", server["meta"])
        self.assertEqual(len(server["networks"]), 2)
        self.assertTrue(all(d["path"].startswith("artifacts/demo-server/") for d in server["extra_disks"]))
        self.assertEqual(server["cloudimg_config"]["hostname"], "demo-server")
        self.assertNotEqual(server["ssh_provision"]["ssh_host_port"], cfg["vms"]["lvm-lab-server"]["ssh_provision"]["ssh_host_port"])
        user_labs.remove("demo")
        self.assertEqual(catalog.read_document()["bases"], {})

    def test_manual_iso_warning(self):
        self.members = ["server=windows-11", "router=pfsense-lab"]
        with mock.patch.object(iso, "iso_source_kind", return_value="manual"):
            self.create(dry_run=True)
        self.assertIn("demo-server requires a user-supplied ISO", self.output.getvalue())
        self.assertIn("demo-router requires a user-supplied ISO", self.output.getvalue())
        self.assertFalse(self.local.exists())

    def test_tracked_source_preserves_placeholders_without_personal_overrides(self):
        path = self.config_dir / "profiles/debian.json"
        fixture = json.loads(path.read_text())
        fixture["vms"]["debian-server"]["ssh_provision"]["post_install_run"] = ["id {{user}}"]
        path.write_text(json.dumps(fixture))
        self.local.write_text(json.dumps({"identity": {"user": "fixtureuser", "password_hash": "fixture-hash"},
                                          "vms": {"debian-server": {"memory_mb": 8192}}}))
        self.assertEqual(config.load_config()["vms"]["debian-server"]["ssh_provision"]["user"], "fixtureuser")
        self.members[0] = "server=debian-server"
        self.create()
        document = catalog.read_document()
        snapshot = document["bases"]["demo-server-base"]
        tracked = config.load_tracked()["debian-server"]
        self.assertEqual(snapshot["memory_mb"], tracked["memory_mb"])
        self.assertEqual(snapshot["preseed_config"]["password_hash"], tracked["preseed_config"]["password_hash"])
        self.assertIn("{{user}}", json.dumps(snapshot))
        self.assertNotIn("fixtureuser", json.dumps(snapshot))
        self.assertNotIn("fixture-hash", json.dumps(snapshot))
        document["vms"]["demo-server"].update(ssh_provision={"user": "anotherfixture", "ssh_host_port": 31000},
                                               preseed_config={"username": "anotherfixture"})
        effective = config.load_config(local_profiles=document)["vms"]["demo-server"]
        self.assertEqual(effective["ssh_provision"]["post_install_run"], ["id anotherfixture"])
        self.assertNotIn("{{user}}", json.dumps(effective))

    def test_local_only_source_remains_supported(self):
        self.local.write_text(json.dumps({"vms": {"personal": {"name": "Local source", "extends": "ubuntu-cloud-base",
                                                               "memory_mb": 1536, "ssh_provision": {"ssh_host_port": 31000}}}}))
        self.members[0] = "server=personal"
        self.create()
        self.assertEqual(config.load_config()["vms"]["demo-server"]["memory_mb"], 1536)

    def test_web_remove_requires_confirmation(self):
        with self.assertRaisesRegex(VMError, "confirm it first"):
            webui.prepare_command(["group", "remove", "demo"], False)
        command, vm = webui.prepare_command(["group", "remove", "demo"], True)
        self.assertIn("remove", command)
        self.assertIsNone(vm)

    def test_invalid_requests_do_not_write(self):
        for group, members in [("../bad", self.members), ("vpn-lab", self.members), ("proxmox-lab", self.members),
                               ("demo", ["bad"]), ("demo", [self.members[0]] * 2),
                               ("demo", ["server=unknown", self.members[1]]),
                               ("demo", ["../bad=ubuntu-cloud-base", self.members[1]])]:
            with self.subTest(group=group, members=members), self.assertRaises(VMError):
                user_labs.create(group, members)
            self.assertFalse(self.local.exists())
            self.assertFalse(self.directory.exists())

    def test_local_content_name_collision_including_tracked_lab_without_content(self):
        for group in ("vpn-lab", "proxmox-lab"):
            path = self.config_dir / "labs.local" / group
            path.mkdir(parents=True)
            (path / "lab.json").write_text(json.dumps({"title": "Collision", "members": ["testvm"]}))
            for fn in (labs.content_groups, lambda: labs.load_content(group)):
                with self.assertRaisesRegex(VMError, "conflicts with a tracked"):
                    fn()
            shutil.rmtree(path)

    def test_tracked_only_ignores_local_content_and_refuses_mutations(self):
        self.create()
        with mock.patch.dict(os.environ, {config.TRACKED_ONLY_ENV: "1"}):
            self.assertNotIn("demo", labs.content_groups())
            self.assertIsNone(labs.load_content("demo"))
            with self.assertRaisesRegex(VMError, "unset VMCTL_TRACKED_ONLY"):
                user_labs.remove("demo")
            with self.assertRaisesRegex(VMError, "unset VMCTL_TRACKED_ONLY"):
                user_labs.create("other", self.members)

    def test_failed_validation_or_write_leaves_no_lab(self):
        real_load = config.load_config
        def validate(*, local_profiles=None):
            if local_profiles and "demo-server" in local_profiles["vms"]:
                raise VMError("candidate invalid")
            return real_load(local_profiles=local_profiles)
        with mock.patch.object(config, "load_config", side_effect=validate), self.assertRaisesRegex(VMError, "candidate invalid"):
            self.create()
        self.assertFalse(self.directory.exists())
        with mock.patch.object(catalog, "write_document", side_effect=OSError("write failed")), self.assertRaises(OSError):
            self.create()
        self.assertFalse(self.local.exists())
        self.assertFalse(self.directory.exists())

    def test_revision_change_refuses_to_overwrite(self):
        with mock.patch.object(user_labs, "_revision", side_effect=["old", "new"]), self.assertRaisesRegex(VMError, "Profiles changed"):
            self.create()
        self.assertFalse(self.local.exists())
        self.assertFalse(self.directory.exists())

    def test_remove_dry_run_real_and_disk_guard(self):
        self.create()
        before = self.local.read_bytes()
        user_labs.remove("demo", dry_run=True)
        self.assertEqual(self.local.read_bytes(), before)
        self.assertTrue(self.directory.exists())
        disk = self.root / "artifacts/demo-client/disk.qcow2"
        disk.parent.mkdir(parents=True)
        disk.touch()  # Even an empty/prepared disk must be cleaned explicitly.
        with self.assertRaisesRegex(VMError, "vmctl group clean demo"):
            user_labs.remove("demo")
        self.assertEqual(self.local.read_bytes(), before)
        disk.unlink()
        user_labs.remove("demo")
        self.assertFalse(self.directory.exists())
        self.assertNotIn("demo-client", config.load_config()["vms"])
        self.assertEqual(self.local.with_suffix(".json.bak").read_bytes(), before)

    def test_remove_extra_disk_guard_and_write_rollback(self):
        self.members[0] = "server=lvm-lab-server"
        self.create()
        before = self.local.read_bytes()
        extra = config.load_config()["vms"]["demo-server"]["extra_disks"][0]["path"]
        disk = self.root / extra
        disk.parent.mkdir(parents=True)
        disk.touch()
        with self.assertRaisesRegex(VMError, "group clean demo"):
            user_labs.remove("demo")
        disk.unlink()
        with mock.patch.object(catalog, "write_document", side_effect=OSError("write failed")), self.assertRaises(OSError):
            user_labs.remove("demo")
        self.assertTrue(self.directory.exists())
        self.assertEqual(self.local.read_bytes(), before)

    def test_remove_refuses_tracked_and_missing_labs(self):
        for name in ("vpn-lab", "absent"):
            with self.assertRaisesRegex(VMError, "not a removable local lab"):
                user_labs.remove(name)

    def test_remove_refuses_a_base_still_used_elsewhere(self):
        self.members[0] = "server=ubuntu-24.04-cloud"
        self.create()
        doc = catalog.read_document()
        doc["vms"]["dependent"] = {"extends": "demo-server-base", "name": "Dependent",
                                     "ssh_provision": {"ssh_host_port": 30000}}
        catalog.write_document(doc)
        before = self.local.read_bytes()
        with self.assertRaisesRegex(VMError, "unknown base 'demo-server-base'"):
            user_labs.remove("demo")
        self.assertEqual(self.local.read_bytes(), before)
        self.assertTrue(self.directory.exists())
