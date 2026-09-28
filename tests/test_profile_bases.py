"""Profile bases: a recipe written once in ``bases`` and extended by the profiles built on it."""
import json
from pathlib import Path
from unittest import mock

import vmctl.config
import vmctl.profile_versions
from tests._common import BaseVmctlTestCase
from vmctl import profile_bases
from vmctl.errors import VMError


class ResolveTests(BaseVmctlTestCase):
    def test_a_child_merges_over_its_base_lists_append_and_name_is_replaced(self):
        bases = {"desk": {"disk": {"path": "artifacts/{{name}}/disk.qcow2", "size": "30G"}, "memory_mb": 2048,
                          "cloud_init": {"write_files": [{"path": "/etc/sudoers.d/lab"}], "runcmd": ["set-default"]}}}
        vms = {"kde": {"extends": "desk", "name": "KDE", "disk": {"size": "40G"},
                       "cloud_init": {"write_files": [{"path": "/etc/sddm.conf"}], "runcmd": ["enable sddm"]}},
               "plain": {"name": "Plain", "disk": {"path": "artifacts/{{name}}/x.img"}}}
        out = profile_bases.resolve_extends(vms, bases)
        self.assertEqual(out["kde"]["disk"], {"path": "artifacts/kde/disk.qcow2", "size": "40G"})
        self.assertEqual(out["kde"]["memory_mb"], 2048)
        self.assertEqual([w["path"] for w in out["kde"]["cloud_init"]["write_files"]], ["/etc/sudoers.d/lab", "/etc/sddm.conf"])
        self.assertEqual(out["kde"]["cloud_init"]["runcmd"], ["set-default", "enable sddm"])
        self.assertEqual(out["kde"]["extends"], "desk")  # kept: the catalog can say what it is built on
        self.assertEqual(out["plain"]["disk"]["path"], "artifacts/plain/x.img")  # {{name}} works without a base too
        self.assertNotIn("extends", out["plain"])
        self.assertEqual(bases["desk"]["disk"]["size"], "30G")  # inputs untouched

    def test_a_base_may_extend_a_base_and_the_chain_is_known(self):
        bases = {"core": {"memory_mb": 1024, "video": {"default": "std"}, "cloud_init": {"runcmd": ["a"]}},
                 "release": {"extends": "core", "iso": "isos/x.iso", "cloud_init": {"runcmd": ["b"]}}}
        vms = {"vm": {"extends": "release", "name": "VM", "cloud_init": {"runcmd": ["c"]}}}
        out = profile_bases.resolve_extends(vms, bases)
        self.assertEqual(out["vm"]["cloud_init"]["runcmd"], ["a", "b", "c"])
        self.assertEqual((out["vm"]["memory_mb"], out["vm"]["iso"]), (1024, "isos/x.iso"))
        self.assertEqual(profile_bases.chain_of("vm", vms, bases), ["release", "core"])
        self.assertEqual(profile_bases.chain_of("release", vms, bases), ["core"])
        self.assertEqual(profile_bases.chain_of("core", vms, bases), [])

    def test_unknown_base_cycle_and_a_name_used_twice_are_named(self):
        with self.assertRaisesRegex(VMError, "vm: extends unknown base 'nope' \\(bases: core\\)"):
            profile_bases.resolve_extends({"vm": {"extends": "nope"}}, {"core": {}})
        with self.assertRaisesRegex(VMError, "base 'a' extends itself: vm -> a -> b -> a"):
            profile_bases.resolve_extends({"vm": {"extends": "a"}}, {"a": {"extends": "b"}, "b": {"extends": "a"}})
        with self.assertRaisesRegex(VMError, "'core' is both a VM profile and a base"):
            profile_bases.resolve_extends({"core": {}}, {"core": {}})
        with self.assertRaisesRegex(VMError, "vm: 'extends' must be the name of a base"):
            profile_bases.resolve_extends({"vm": {"extends": 3}}, {})
        for key in ("version", "verified"):  # earned per profile, never inherited
            with self.assertRaisesRegex(VMError, f"base 'core': meta.{key} belongs to each profile"):
                profile_bases.resolve_extends({"vm": {"extends": "core"}}, {"core": {"meta": {key: "1.0.0"}}})


class LoadConfigTests(BaseVmctlTestCase):
    def write_catalog(self, local=None):
        base = {key: value for key, value in self.vm_config.items() if key not in ("name", "disk", "firmware")}
        base["disk"] = {**self.vm_config["disk"], "path": "artifacts/{{name}}/disk.qcow2"}
        base["firmware"] = dict(self.vm_config["firmware"])
        if "vars_path" in base["firmware"]:
            base["firmware"]["vars_path"] = "artifacts/{{name}}/OVMF_VARS.fd"
        document = {"bases": {"test-base": base},
                    "vms": {"child-a": {"extends": "test-base", "name": "A", "memory_mb": 777},
                            "child-b": {"extends": "test-base", "name": "B", "disk": {"size": "99G"}}}}
        (self.config_dir / "profiles" / "test.json").write_text(json.dumps(document), encoding="utf-8")
        if local is not None:
            (self.config_dir / "profiles" / "local.json").write_text(json.dumps(local), encoding="utf-8")

    def test_the_children_are_the_vms_the_base_is_not_and_paths_follow_the_name(self):
        self.write_catalog()
        cfg = vmctl.config.load_config()
        self.assertEqual(sorted(cfg["vms"]), ["child-a", "child-b"])
        self.assertEqual(cfg["vms"]["child-a"]["memory_mb"], 777)
        self.assertEqual(cfg["vms"]["child-a"]["disk"]["path"], "artifacts/child-a/disk.qcow2")
        self.assertEqual(cfg["vms"]["child-b"]["disk"]["size"], "99G")
        self.assertEqual(cfg["vms"]["child-b"]["memory_mb"], self.vm_config["memory_mb"])
        with self.assertRaises(VMError):
            vmctl.config.get_vm(cfg, "test-base")

    def test_a_local_override_applies_on_top_of_the_resolved_profile(self):
        self.write_catalog(local={"vms": {"child-a": {"memory_mb": 4096, "cpus": 7}}})
        cfg = vmctl.config.load_config()
        self.assertEqual((cfg["vms"]["child-a"]["memory_mb"], cfg["vms"]["child-a"]["cpus"]), (4096, 7))
        self.assertEqual(cfg["vms"]["child-a"]["disk"]["path"], "artifacts/child-a/disk.qcow2")
        # A wholly local profile may extend a tracked base too.
        self.write_catalog(local={"vms": {"mine": {"extends": "test-base", "name": "Mine"}}})
        cfg = vmctl.config.load_config()
        self.assertEqual(cfg["vms"]["mine"]["disk"]["path"], "artifacts/mine/disk.qcow2")

    def test_load_tracked_resolves_without_local_json_and_keeps_the_user_placeholder(self):
        self.write_catalog(local={"vms": {"child-a": {"memory_mb": 1}}})
        tracked = vmctl.config.load_tracked()
        self.assertEqual(tracked["child-a"]["memory_mb"], 777)  # local.json never seen
        self.assertEqual(tracked["child-a"]["extends"], "test-base")
        self.assertNotIn("test-base", tracked)

    def test_a_base_written_twice_or_a_bad_extends_fails_the_load(self):
        self.write_catalog()
        (self.config_dir / "profiles" / "other.json").write_text(json.dumps({"bases": {"test-base": {}}, "vms": {}}), encoding="utf-8")
        with self.assertRaisesRegex(VMError, "Duplicate base 'test-base'"):
            vmctl.config.load_config()
        (self.config_dir / "profiles" / "other.json").write_text(json.dumps({"bases": [], "vms": {}}), encoding="utf-8")
        with self.assertRaisesRegex(VMError, "'bases' must be an object"):
            vmctl.config.load_config()


class VersionTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        base = {key: value for key, value in self.vm_config.items() if key not in ("name", "memory_mb")}
        document = {"bases": {"core": {"memory_mb": 1024}, "desk": {"extends": "core", **base}},
                    "vms": {"one": {"extends": "desk", "name": "One", "meta": {"version": "1.0.0"}},
                            "two": {"extends": "desk", "name": "Two", "meta": {"version": "1.0.0"}, "cpus": 3},
                            "alone": {**self.vm_config, "meta": {"version": "1.0.0"}}}}
        self.path = self.config_dir / "profiles" / "test.json"
        self.path.write_text(json.dumps(document), encoding="utf-8")
        vmctl.profile_versions.init("first", self.root, date="2026-09-28")

    def test_the_fingerprint_is_the_resolved_recipe_so_a_base_edit_names_every_child(self):
        self.assertEqual(vmctl.profile_versions.check(self.root), [])
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["bases"]["core"]["memory_mb"] = 2048
        self.path.write_text(json.dumps(document), encoding="utf-8")
        problems = vmctl.profile_versions.check(self.root)
        self.assertEqual([p.split(":")[0] for p in problems], ["one", "two"])
        self.assertEqual(vmctl.profile_versions.children_of("core", self.root), ["one", "two"])
        self.assertEqual(vmctl.profile_versions.children_of("desk", self.root), ["one", "two"])
        with self.assertRaisesRegex(VMError, "alone is not a base"):
            vmctl.profile_versions.children_of("alone", self.root)

    def test_the_tool_bumps_every_child_of_a_base_at_once(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("bump_profile", Path(__file__).resolve().parent.parent / "tools" / "bump_profile.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch("sys.stdout"):
            self.assertEqual(module.main(["core", "minor", "--children", "-m", "more memory", "--root", str(self.root)]), 0)
        lock = vmctl.profile_versions.read_lock(self.root)["profiles"]
        self.assertEqual((lock["one"]["version"], lock["two"]["version"], lock["alone"]["version"]), ("1.1.0", "1.1.0", "1.0.0"))
        self.assertEqual(lock["two"]["history"][0]["note"], "more memory")
        self.assertEqual(vmctl.profile_versions.check(self.root), [])

    def test_a_base_nothing_extends_is_reported(self):
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["bases"]["spare"] = {"memory_mb": 1}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(vmctl.profile_versions.check(self.root), ["spare: a base nothing extends (drop it, or extend it)"])
