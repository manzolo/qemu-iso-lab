"""Profile groups: the categories `check-vms --group` runs instead of the whole matrix."""

import argparse
import io
import json
import shutil
import sys
import tempfile
import re
import unittest
from pathlib import Path
from unittest import mock

import vmctl.config
import vmctl.errors
import vmctl.lifecycle
import vmctl.pvecluster
import vmctl.report
import vmctl.state
from _common import BaseVmctlTestCase

ROOT = Path(__file__).resolve().parent.parent


class GroupDefinitionTests(BaseVmctlTestCase):
    def profile(self, **meta):
        vm = json.loads(json.dumps(self.vm_config))
        vm["meta"] = meta
        return vm

    def test_meta_groups_must_be_unique_lowercase_slugs(self):
        for groups, expected in (
            ("ubuntu", "list of strings"),
            ([1], "list of strings"),
            (["ubuntu", "ubuntu"], "must not repeat"),
            (["Ubuntu"], "lowercase"),
            (["-lead"], "lowercase"),
            (["with space"], "lowercase"),
        ):
            with self.subTest(groups=groups):
                errors = self.vmctl.validate_vm_profile("v", self.profile(status="manual", groups=groups))
                self.assertTrue(any(expected in error for error in errors), errors)
        self.assertEqual(self.vmctl.validate_vm_profile("v", self.profile(status="manual", groups=["ubuntu", "ubuntu-releases"])), [])
        self.assertEqual(self.vmctl.declared_groups(self.profile(status="manual", groups=["a", "b"])), ["a", "b"])
        self.assertEqual(self.vmctl.declared_groups(self.profile(status="manual")), [])

    def test_groups_are_derived_from_family_status_role_and_install_flow(self):
        vm = self.profile(family="debian", role="server", status="unattended", groups=["ubuntu"])
        vm["preseed_config"] = {"username": "lab"}
        vm["ssh_provision"] = {"user": "lab", "ssh_host_port": 2299}
        groups = self.vmctl.profile_groups(vm)
        self.assertEqual(groups, {
            "declared": ["ubuntu"],
            "family": ["debian"],
            "status": ["unattended"],
            "role": ["server"],
            "flow": ["bootstrap-preseed"],
        })
        # A profile the matrix cannot run contributes no flow group.
        template = self.profile(family="windows", role="import-template", status="manual")
        self.assertEqual(self.vmctl.profile_groups(template)["flow"], [])
        # Status defaults to manual, so every profile lands in exactly one status group.
        self.assertEqual(self.vmctl.profile_groups(self.profile())["status"], ["manual"])

    def test_index_and_sources_merge_declared_and_derived_membership(self):
        cfg = {"vms": {
            "alpha": self.profile(family="debian", role="server", status="unattended", groups=["squad"]),
            "beta": self.profile(family="debian", role="desktop", status="manual"),
            "squad": self.profile(family="squad", role="desktop", status="manual"),
        }}
        index = self.vmctl.group_index(cfg)
        self.assertEqual(index["debian"], ["alpha", "beta"])
        self.assertEqual(index["desktop"], ["beta", "squad"])
        self.assertEqual(index["unattended"], ["alpha"])
        # One name, two origins: the declared tag and the family of another profile.
        self.assertEqual(index["squad"], ["alpha", "squad"])
        self.assertEqual(self.vmctl.group_sources(cfg)["squad"], ["declared", "family"])
        self.assertEqual(self.vmctl.group_sources(cfg)["debian"], ["family"])

    def test_selection_is_the_union_in_catalog_order_and_unknown_names_list_what_exists(self):
        cfg = {"vms": {
            "alpha": self.profile(family="debian", status="manual", groups=["squad"]),
            "beta": self.profile(family="debian", status="manual"),
            "gamma": self.profile(family="arch", status="manual", groups=["squad"]),
        }}
        self.assertEqual(self.vmctl.resolve_group_selection(cfg, ["squad"]), ["alpha", "gamma"])
        self.assertEqual(self.vmctl.resolve_group_selection(cfg, ["squad", "debian"]), ["alpha", "gamma", "beta"])
        with self.assertRaises(self.vmctl.VMError) as caught:
            self.vmctl.resolve_group_selection(cfg, ["squad", "nope"])
        message = str(caught.exception)
        self.assertIn("nope", message)
        self.assertNotIn("squad,", message.split("Available:")[0])  # only the unknown one is blamed
        self.assertIn("arch", message.split("Available:")[1])

    def test_list_groups_prints_and_serialises_the_catalog(self):
        self.write_config_dir()
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.vmctl.cmd_list(argparse.Namespace(groups=True, json=False))
        text = stdout.getvalue()
        self.assertIn("GROUP", text)
        self.assertIn("manual", text)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.vmctl.cmd_list(argparse.Namespace(groups=True, json=True))
        rows = {row["group"]: row for row in json.loads(stdout.getvalue())}
        self.assertEqual(rows["manual"]["profiles"], [self.vm_name])
        self.assertEqual(rows["manual"]["sources"], ["status"])


class GroupSelectionTests(BaseVmctlTestCase):
    def catalog(self):
        def vm(port, **meta):
            profile = json.loads(json.dumps(self.vm_config))
            profile["meta"] = meta
            profile["preseed_config"] = {"username": "lab"}
            profile["ssh_provision"] = {"user": "lab", "ssh_host_port": port}
            return profile
        self.write_extra_profile("groups.json", {"vms": {
            "steady": vm(2301, family="debian", status="unattended", groups=["squad"]),
            "shaky": vm(2302, family="debian", status="experimental", groups=["squad"]),
            "outsider": vm(2303, family="arch", status="unattended"),
        }})

    def run_matrix(self, **overrides):
        args = argparse.Namespace(vms=[], group=None, timeout=300, parallel=1, dry_run=True,
                                  clean_first=False, no_clean_first=True)
        for key, value in overrides.items():
            setattr(args, key, value)
        with mock.patch.object(vmctl.lifecycle, "run_local_test_once", return_value=("passed", "-")) as run, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(self.vmctl.cmd_test_local(args), 0)
        return [call.args[0] for call in run.call_args_list]

    def test_a_group_runs_its_profiles_and_still_sets_experimental_ones_aside(self):
        self.catalog()
        self.assertEqual(self.run_matrix(group=["squad"]), ["steady"])

    def test_naming_an_experimental_profile_runs_it_even_alongside_a_group(self):
        self.catalog()
        # A name is a choice ("run this one"); a group is a selector like the full matrix.
        self.assertEqual(self.run_matrix(vms=["shaky"]), ["shaky"])
        self.assertEqual(sorted(self.run_matrix(vms=["shaky"], group=["squad"])), ["shaky", "steady"])

    def test_groups_and_names_are_merged_without_duplicates(self):
        self.catalog()
        self.assertEqual(self.run_matrix(vms=["steady"], group=["squad", "arch"]), ["steady", "outsider"])

    def test_without_names_or_groups_the_whole_catalog_runs(self):
        self.catalog()
        self.assertEqual(sorted(self.run_matrix()), ["outsider", "steady", self.vm_name])


class ClusterCheckTests(BaseVmctlTestCase):
    """check-vms forms a Proxmox cluster whose nodes all passed, as one more row."""

    def setUp(self):
        super().setUp()
        def node(port):
            profile = json.loads(json.dumps(self.vm_config))
            profile["ssh_provision"] = {"user": "root", "ssh_host_port": port}
            return profile
        self.write_extra_profile("nodes.json", {"vms": {"node-a": node(2301), "node-b": node(2302)}})
        self.entry = {"pve-lab": {"primary": "node-a", "nodes": ["node-a", "node-b"]}}

    def run_checks(self, selected, outcomes, form_error=None):
        cfg = vmctl.config.load_config()
        results = [(name, status, "-") for name, status in outcomes.items()]
        args = argparse.Namespace(timeout=300, dry_run=False)
        events = []
        with mock.patch.object(vmctl.pvecluster, "clusters", return_value=self.entry), \
             mock.patch.object(vmctl.pvecluster, "form", side_effect=form_error or (lambda cfg, nodes, *a, **k: events.append(("form", nodes)))) as form, \
             mock.patch.object(vmctl.lifecycle, "group_states", side_effect=lambda cfg, names: {n: {"running": False} for n in names}), \
             mock.patch.object(vmctl.lifecycle, "cmd_start", side_effect=lambda a: events.append(("start", a.vm))), \
             mock.patch.object(vmctl.lifecycle, "cmd_stop", side_effect=lambda a: events.append(("stop", a.vm))), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            vmctl.lifecycle.run_cluster_checks(cfg, selected, results, args)
        return results[len(outcomes):], events, form

    def test_a_cluster_whose_nodes_passed_is_formed_and_the_nodes_stopped(self):
        rows, events, _ = self.run_checks(["node-a", "node-b"], {"node-a": "passed", "node-b": "passed"})
        self.assertEqual(rows, [("cluster-pve-lab", "passed", "2 nodes, quorate")])
        self.assertEqual(events, [("start", "node-a"), ("start", "node-b"), ("form", ["node-a", "node-b"]),
                                  ("stop", "node-b"), ("stop", "node-a")])

    def test_a_node_that_did_not_pass_skips_the_cluster_row(self):
        rows, events, form = self.run_checks(["node-a", "node-b"], {"node-a": "passed", "node-b": "failed"})
        self.assertEqual(rows, [("cluster-pve-lab", "skipped", "skipped: node-b did not pass")])
        form.assert_not_called()
        self.assertEqual(events, [])

    def test_a_run_naming_only_some_nodes_has_no_cluster_row(self):
        rows, events, form = self.run_checks(["node-a"], {"node-a": "passed"})
        self.assertEqual(rows, [])
        form.assert_not_called()

    def test_a_cluster_that_fails_is_a_failed_row_and_the_nodes_still_stop(self):
        rows, events, _ = self.run_checks(["node-a", "node-b"], {"node-a": "passed", "node-b": "passed"},
                                          form_error=vmctl.errors.VMError("no quorum"))
        self.assertEqual(rows, [("cluster-pve-lab", "failed", "no quorum")])
        self.assertEqual(events[-2:], [("stop", "node-b"), ("stop", "node-a")])

    def test_the_report_row_is_not_demoted_for_having_no_screenshot(self):
        directory = Path(self.tempdir.name) / "report"
        (directory / "results").mkdir(parents=True)
        args = argparse.Namespace(dry_run=False, _report_dir=str(directory))
        vmctl.report.record_group_row("cluster-pve-lab", "Proxmox cluster pve-lab", {"meta": {"status": "unattended"}},
                                      args, "passed", "2 nodes, quorate", 12.0, "group cluster")
        result = json.loads((directory / "results" / "cluster-pve-lab.json").read_text())
        self.assertEqual((result["status"], result["phase"], result["screenshot"]), ("PASS", "cluster", None))


class RepositoryGroupTests(unittest.TestCase):
    """The tracked catalog: a group that silently loses members is worse than no group."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        profiles = Path(cls.tmp.name) / "vms" / "profiles"
        profiles.mkdir(parents=True)
        for path in (ROOT / "vms" / "profiles").glob("*.json"):
            if path.name != "local.json":
                shutil.copy(path, profiles / path.name)
        original = (vmctl.state.ROOT, vmctl.state.CONFIG_DIR)
        try:
            vmctl.state.ROOT = Path(cls.tmp.name)
            vmctl.state.CONFIG_DIR = Path(cls.tmp.name) / "vms"
            cls.cfg = vmctl.config.load_config()
        finally:
            vmctl.state.ROOT, vmctl.state.CONFIG_DIR = original
        cls.index = vmctl.lifecycle.group_index(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_ubuntu_profile_carries_the_ubuntu_group(self):
        # Ubuntu and its official flavours all spell "buntu"; meta.family says "debian" for
        # every one of them, so without this tag a new flavour would quietly miss the group.
        expected = sorted(name for name in self.cfg["vms"] if "buntu" in name)
        self.assertEqual(sorted(self.index["ubuntu"]), expected)
        self.assertGreater(len(expected), 20)

    def test_ubuntu_releases_holds_one_desktop_install_per_release(self):
        releases = self.index["ubuntu-releases"]
        self.assertIn("ubuntu-gnome-24.04", releases)  # 24.04's entry of the series
        self.assertIn("ubuntu-26.04", releases)
        versions = sorted(name.split("-")[1] for name in releases if re.fullmatch(r"ubuntu-\d+\.\d+", name))
        self.assertEqual(versions, ["10.04", "12.04", "14.04", "16.04", "18.04", "20.04", "22.04", "26.04", "8.04"])
        for name in releases:
            self.assertEqual(vmctl.config.get_vm(self.cfg, name)["meta"]["status"], "unattended")

    def test_declared_groups_exist_and_are_documented(self):
        declared = sorted({group for _, vm in vmctl.config.sorted_vm_items(self.cfg)
                           for group in vmctl.config.declared_groups(vm)})
        self.assertEqual(declared, ["debian-only", "kali", "netlab", "proxmox-lab", "smoke", "ubuntu",
                                    "ubuntu-flavors", "ubuntu-releases", "windows-retro"])
        documentation = (ROOT / "docs" / "UNATTENDED.md").read_text(encoding="utf-8")
        for group in declared:
            self.assertIn(f"`{group}`", documentation, f"{group} is not documented")

    def test_smoke_runs_one_profile_per_flow_that_downloads_its_own_medium(self):
        flows = [vmctl.lifecycle.local_test_mode(vmctl.config.get_vm(self.cfg, name))[0]
                 for name in self.index["smoke"]]
        self.assertEqual(sorted(flows), sorted(set(flows)), "smoke must not test a flow twice")
        self.assertIn("boot-check", flows)
        for flow in ("bootstrap-unattended", "bootstrap-preseed", "bootstrap-kickstart",
                     "bootstrap-archinstall", "bootstrap-alpine", "bootstrap-autoyast",
                     "bootstrap-nixos", "bootstrap-freebsd"):
            self.assertIn(flow, flows)
        for name in self.index["smoke"]:
            self.assertTrue(vmctl.config.get_vm(self.cfg, name).get("iso_url"),
                            f"{name} needs a user-supplied ISO: it cannot be part of smoke")

    def test_one_family_name_per_distro_so_no_group_is_split_in_two(self):
        families = {str((vm.get("meta") or {}).get("family")) for _, vm in vmctl.config.sorted_vm_items(self.cfg)}
        # "suse" alongside "opensuse" once hid the only unattended openSUSE profile from its group.
        for name in families:
            self.assertFalse(any(other != name and other.endswith(name) for other in families),
                             f"{name} looks like a second spelling of another family: {sorted(families)}")

    def test_every_profile_belongs_to_at_least_one_selectable_group(self):
        for name, vm in vmctl.config.sorted_vm_items(self.cfg):
            groups = {group for groups in vmctl.lifecycle.profile_groups(vm).values() for group in groups}
            self.assertTrue(groups, f"{name} is in no group at all")
