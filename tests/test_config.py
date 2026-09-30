import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._common import BaseVmctlTestCase  # noqa: E402


class IdentitySkipsRootOnlyGuestsTests(BaseVmctlTestCase):
    def test_proxmox_and_haiku_keep_their_only_user(self):
        from vmctl import config
        vm = {"proxmox_config": {"hostname": "pve"}, "ssh_provision": {"user": "root"}}
        config.apply_identity(vm, {"user": "tester", "password_hash": "x"}, {})
        self.assertEqual(vm["ssh_provision"]["user"], "root")
        vm = {"haiku_config": {"user": "user"}, "ssh_provision": {"user": "user"}}
        config.apply_identity(vm, {"user": "tester"}, {})
        self.assertEqual(vm["ssh_provision"]["user"], "user")
        vm = {"ssh_provision": {"user": "lab"}}
        config.apply_identity(vm, {"user": "tester"}, {})
        self.assertEqual(vm["ssh_provision"]["user"], "tester")

class LocaleBlockTests(BaseVmctlTestCase):
    def test_validate_locale_accepts_posix_names_and_rejects_the_rest(self):
        from vmctl import config
        from vmctl.errors import VMError
        self.assertEqual(config.validate_locale({"language": "it_IT.UTF-8", "keyboard": "it", "timezone": "Europe/Rome"}, "x"),
                         {"language": "it_IT.UTF-8", "keyboard": "it", "timezone": "Europe/Rome"})
        self.assertEqual(config.validate_locale({"timezone": " UTC "}, "x"), {"timezone": "UTC"})
        for bad in ({"language": "italian"}, {"keyboard": "Italian"}, {"timezone": "Rome time"}, {"lang": "it"}, {"language": ""}, ["it"]):
            with self.subTest(bad=bad), self.assertRaises(VMError):
                config.validate_locale(bad, "x")

    def test_apply_locale_speaks_every_installer_format_and_a_per_vm_override_wins(self):
        from vmctl import config
        locale = {"language": "it_IT.UTF-8", "keyboard": "it", "timezone": "Europe/Rome"}
        vm = {"preseed_config": {"locale": "en_US.UTF-8", "language": "en", "country": "US", "keyboard_layout": "us", "timezone": "UTC"},
              "autoyast_config": {"language": "en_US", "keyboard_layout": "english-us", "timezone": "UTC"},
              "windows_config": {"language": "en-US", "input_locale": "en-US", "timezone": "UTC"},
              "archinstall_config": {"locale_lang": "en_US", "locale_enc": "UTF-8", "keyboard_layout": "us"},
              "kickstart_config": {"locale": "en_US.UTF-8"}, "haiku_config": {"user": "user"}}
        config.apply_locale(vm, locale, {"windows_config": {"timezone": "GMT Standard Time"}})
        # d-i: the installer stays in English on the serial console (it asked "Language:" for an hour with language=it),
        # keyboard and time zone move, the system locale comes as a late command
        self.assertEqual({k: v for k, v in vm["preseed_config"].items() if k != "late_commands"},
                         {"locale": "en_US.UTF-8", "language": "en", "country": "US", "keyboard_layout": "it", "timezone": "Europe/Rome"})
        self.assertEqual(vm["preseed_config"]["late_commands"], [config.preseed_locale_command("it_IT.UTF-8")])
        self.assertIn("update-locale LANG=it_IT.UTF-8", vm["preseed_config"]["late_commands"][0])
        config.apply_locale(vm, locale, {"windows_config": {"timezone": "GMT Standard Time"}})  # applied twice: the late command is added once
        self.assertEqual(len(vm["preseed_config"]["late_commands"]), 1)
        self.assertEqual(vm["autoyast_config"], {"language": "it_IT", "keyboard_layout": "italian", "timezone": "Europe/Rome"})
        self.assertEqual(vm["windows_config"], {"language": "en-US", "input_locale": "it-IT", "timezone": "UTC"})  # an overridden field is left alone (the override merges on top), the UI language stays
        self.assertEqual(vm["archinstall_config"], {"locale_lang": "it_IT", "locale_enc": "UTF-8", "keyboard_layout": "it"})  # no timezone field: none added
        self.assertEqual(vm["kickstart_config"], {"locale": "it_IT.UTF-8"})
        self.assertEqual(vm["haiku_config"], {"user": "user"})
        # an unknown keyboard leaves AutoYaST's field alone, an unknown zone Windows'
        vm = {"autoyast_config": {"keyboard_layout": "english-us"}, "windows_config": {"timezone": "UTC"}}
        config.apply_locale(vm, {"keyboard": "xx", "timezone": "Mars/Olympus"}, {})
        self.assertEqual(vm, {"autoyast_config": {"keyboard_layout": "english-us"}, "windows_config": {"timezone": "UTC"}})

    def test_load_config_applies_the_locale_of_local_json_to_tracked_profiles_only(self):
        from vmctl import config
        self.write_extra_profile("more.json", {"vms": {"withpreseed": {**self.vm_config, "name": "P", "preseed_config": {"username": "lab", "locale": "en_US.UTF-8", "timezone": "UTC"},
                                                                        "disk": {**self.vm_config["disk"], "path": "artifacts/withpreseed/disk.qcow2"}}}})
        local = {"locale": {"language": "it_IT.UTF-8", "timezone": "Europe/Rome"},
                 "vms": {"withpreseed": {"preseed_config": {"timezone": "Europe/Berlin"}},
                         "mine": {**self.vm_config, "name": "M", "preseed_config": {"username": "lab", "locale": "en_US.UTF-8"}, "disk": {**self.vm_config["disk"], "path": "artifacts/mine/disk.qcow2"}}}}
        cfg = config.load_config(local_profiles=local)
        self.assertEqual(cfg["vms"]["withpreseed"]["preseed_config"]["locale"], "en_US.UTF-8")  # the installer's own language stays
        self.assertIn("LANG=it_IT.UTF-8", cfg["vms"]["withpreseed"]["preseed_config"]["late_commands"][0])
        self.assertEqual(cfg["vms"]["withpreseed"]["preseed_config"]["timezone"], "Europe/Berlin")  # the per-VM entry wins
        self.assertEqual(cfg["vms"]["mine"]["preseed_config"]["locale"], "en_US.UTF-8")  # a local-only VM is untouched

class ManualReasonTests(BaseVmctlTestCase):
    def test_meta_manual_is_validated(self):
        from vmctl import config
        base = {**self.vm_config}
        ok = {**base, "meta": {"status": "manual", "manual": "live"}}
        self.assertEqual(config.validate_vm_profile("v", ok), [])
        twin = {**base, "meta": {"status": "manual", "manual": "twin", "automated_as": "other"}}
        self.assertEqual(config.validate_vm_profile("v", twin), [])
        for meta, message in (({"status": "manual", "manual": "nope"}, "must be one of"),
                              ({"status": "manual", "manual": "twin"}, "automated_as"),
                              ({"status": "unattended", "manual": "todo"}, "manual profiles only"),
                              ({"status": "manual", "automated_as": "x"}, "goes with meta.manual")):
            with self.subTest(meta=meta):
                problems = config.validate_vm_profile("v", {**base, "meta": meta})
                self.assertTrue(any(message in p for p in problems), problems)

class ConfigTests(BaseVmctlTestCase):
    def test_rejects_invalid_profile_status_and_verification_date(self):
        from vmctl import config
        for meta in (None, {"status": "passed"}, {"status": []}, {"verified": "2026-02-30"},
                     {"verified": "20260907"}, {"verified": True}):
            with self.subTest(meta=meta):
                self.assertTrue(config.validate_vm_profile("test", self.vm_config | {"meta": meta}))
        self.assertEqual(config.validate_vm_profile("test", self.vm_config | {
            "meta": {"status": "unattended", "verified": "2026-09-07"}}), [])

    def test_deprecated_profile_resolves_with_one_stderr_warning(self):
        from vmctl import config
        cfg = {"vms": {"arch-dms": self.vm_config}}
        with mock.patch("sys.stderr") as stderr:
            self.assertIs(config.get_vm(cfg, "arch-dms-local"), self.vm_config)
        message = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertEqual(message, "warning: profile 'arch-dms-local' is deprecated; use 'arch-dms'\n")

    def test_legacy_local_override_merges_into_canonical_profile(self):
        from vmctl import config
        self.vm_name = "arch-dms"
        self.write_config_dir()
        local = self.root / "vms/profiles/local.json"
        local.write_text(json.dumps({"vms": {"arch-dms-local": {"memory_mb": 1234}}}))
        with mock.patch("sys.stderr"):
            cfg = config.load_config()
        self.assertEqual(cfg["vms"]["arch-dms"]["memory_mb"], 1234)
        self.assertNotIn("arch-dms-local", cfg["vms"])
        local.write_text(json.dumps({"vms": {"arch-dms-local": {}, "arch-dms": {}}}))
        with mock.patch("sys.stderr"), self.assertRaisesRegex(self.vmctl.VMError, "Conflicting local overrides"):
            config.load_config()

    def test_load_config_reads_profiles_from_config_dir(self):
        config = self.vmctl.load_config()

        self.assertIn(self.vm_name, config["vms"])
        self.assertNotIn("catalog", config)

    def test_load_config_rejects_profile_missing_required_field(self):
        del self.vm_config["memory_mb"]
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("memory_mb", str(ctx.exception))

    def test_load_config_rejects_invalid_firmware_type(self):
        self.vm_config["firmware"] = {"type": "tianocore"}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("firmware.type", str(ctx.exception))

    def test_load_config_rejects_efi_firmware_missing_paths(self):
        self.vm_config["firmware"] = {"type": "efi"}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        message = str(ctx.exception)
        self.assertIn("firmware.code", message)
        self.assertIn("firmware.vars_template", message)
        self.assertIn("firmware.vars_path", message)

    def test_load_config_rejects_video_default_not_in_variants(self):
        self.vm_config["video"] = {"default": "ghost", "variants": {"std": []}}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("video.default", str(ctx.exception))

    def test_load_config_rejects_invalid_headless_video_args(self):
        for value in (None, [], "egl-headless", ["-display", 1]):
            with self.subTest(value=value):
                self.vm_config["video"]["headless"] = value
                self.write_config_dir()
                with self.assertRaises(self.vmctl.VMError) as ctx:
                    self.vmctl.load_config()
                self.assertIn("video.headless", str(ctx.exception))

    def test_load_config_rejects_installer_order_with_unknown_variant(self):
        self.vm_config["video"]["installer_order"] = ["std", "ghost"]
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("installer_order", str(ctx.exception))

    def test_load_config_aggregates_multiple_errors_in_one_message(self):
        del self.vm_config["memory_mb"]
        self.vm_config["firmware"] = {"type": "uefi"}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        message = str(ctx.exception)
        self.assertIn("memory_mb", message)
        self.assertIn("firmware.type", message)

    def test_load_config_reads_local_profile_override_file(self):
        local_vm = json.loads(json.dumps(self.vm_config))
        local_vm["name"] = "Local VM"
        local_vm["disk"]["path"] = "artifacts/localvm/disk.qcow2"
        self.write_extra_profile("local.json", {"vms": {"localvm": local_vm}})

        config = self.vmctl.load_config()

        self.assertIn("localvm", config["vms"])
        self.assertEqual(config["vms"]["localvm"]["name"], "Local VM")

    def test_local_identity_applies_to_every_tracked_profile_but_per_vm_overrides_win(self):
        # 2026-09-28: fifteen new history profiles were born lab/lab because no per-VM entry named them.
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2222}
        self.vm_config["preseed_config"] = {"username": "lab", "password_hash": "$6$lab"}
        import copy
        def variant(name, port, **sections):
            vm = copy.deepcopy(self.vm_config)
            vm["name"] = name
            vm["disk"]["path"] = f"artifacts/{name}/disk.qcow2"
            vm["ssh_provision"]["ssh_host_port"] = port
            vm.pop("preseed_config")
            vm.update(sections)
            return vm
        windows = variant("win", 2223, windows_config={"username": "lab", "password": "lab", "realname": "Lab User", "edition": "Windows 11 Pro"})
        base = variant("base", 2224, preseed_config={"username": "lab", "password_hash": "$6$lab"})
        base.pop("name")
        self.write_config_dir()
        self.write_extra_profile("more.json", {
            "bases": {"identity-base": base},
            "vms": {"win": windows, "child": {"extends": "identity-base", "name": "Child", "disk": {"path": "artifacts/child/disk.qcow2"}}}})
        local_only = variant("mine", 2225, preseed_config={"username": "other", "password_hash": "$6$other"})
        local_only["ssh_provision"]["user"] = "other"
        self.write_extra_profile("local.json", {
            "identity": {"user": "me", "password": "secret", "password_hash": "$6$me", "realname": "Me Myself"},
            "vms": {"win": {"windows_config": {"password": "explicit"}}, "mine": local_only}})

        vms = self.vmctl.load_config()["vms"]
        self.assertEqual(vms[self.vm_name]["ssh_provision"]["user"], "me")
        self.assertEqual(vms[self.vm_name]["preseed_config"], {"username": "me", "password_hash": "$6$me"})
        self.assertNotIn("windows_config", vms[self.vm_name])  # sections are never created
        self.assertEqual(vms["win"]["windows_config"]["username"], "me")
        self.assertEqual(vms["win"]["windows_config"]["realname"], "Me Myself")
        self.assertEqual(vms["win"]["windows_config"]["password"], "explicit")  # the per-VM entry wins field by field
        self.assertEqual(vms["child"]["preseed_config"]["username"], "me")  # inherited from the base
        self.assertEqual(vms["child"]["ssh_provision"]["user"], "me")
        self.assertEqual(vms["mine"]["preseed_config"]["username"], "other")  # a local-only VM is left alone

    def test_local_identity_leaves_haiku_on_its_only_user(self):
        self.vm_config["ssh_provision"] = {"user": "user", "ssh_host_port": 2280}
        self.vm_config["haiku_config"] = {}
        self.write_config_dir()
        self.write_extra_profile("local.json", {"identity": {"user": "me"}, "vms": {}})
        self.assertEqual(self.vmctl.load_config()["vms"][self.vm_name]["ssh_provision"]["user"], "user")

    def test_tracked_only_ignores_local_json_identity_locale_and_overrides(self):
        # check-vms --tracked-only: the default half of a smoke run done twice
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2222}
        self.vm_config["preseed_config"] = {"username": "lab", "password_hash": "$6$lab", "timezone": "UTC"}
        self.write_config_dir()
        self.write_extra_profile("local.json", {
            "identity": {"user": "me", "password_hash": "$6$me"},
            "locale": {"language": "it_IT.UTF-8", "keyboard": "it", "timezone": "Europe/Rome"},
            "vms": {self.vm_name: {"memory_mb": 12345}}})
        personal = self.vmctl.load_config()["vms"][self.vm_name]
        self.assertEqual((personal["ssh_provision"]["user"], personal["preseed_config"]["timezone"], personal["memory_mb"]),
                         ("me", "Europe/Rome", 12345))
        with mock.patch.dict(os.environ, {self.vmctl.TRACKED_ONLY_ENV: "1"}):
            tracked = self.vmctl.load_config()["vms"][self.vm_name]
        self.assertEqual(tracked["ssh_provision"]["user"], "lab")
        self.assertEqual(tracked["preseed_config"]["timezone"], "UTC")
        self.assertNotEqual(tracked.get("memory_mb"), 12345)

    def test_local_identity_is_validated(self):
        self.write_config_dir()
        for identity in ("me", {}, {"user": ""}, {"user": "me", "shell": "zsh"}, {"user": "{{user}}"}, {"user": "me", "password": 5}):
            with self.subTest(identity=identity):
                self.write_extra_profile("local.json", {"identity": identity, "vms": {}})
                with self.assertRaisesRegex(self.vmctl.VMError, "identity"):
                    self.vmctl.load_config()

    def test_load_config_local_profile_can_override_shared_profile(self):
        self.vm_config["ssh_provision"] = {
            "hostname": "base-vm",
            "user": "vmuser",
            "ssh_host_port": 2222,
            "post_install_run": ["echo base"],
        }
        self.write_config_dir()

        local_vm = {
            "name": "Local Override",
            "ssh_provision": {
                "ssh_key": "~/.ssh/id_rsa",
                "copy_from_host": [{"source": "~/.config/app/", "dest": "/home/vmuser/.config/app"}],
            },
        }
        self.write_extra_profile("local.json", {"vms": {self.vm_name: local_vm}})

        config = self.vmctl.load_config()

        vm = config["vms"][self.vm_name]
        self.assertEqual(vm["name"], "Local Override")
        self.assertEqual(vm["ssh_provision"]["hostname"], "base-vm")
        self.assertEqual(vm["ssh_provision"]["ssh_key"], "~/.ssh/id_rsa")
        self.assertEqual(vm["ssh_provision"]["post_install_run"], ["echo base"])

    def test_load_config_local_profile_concatenates_arrays(self):
        self.vm_config["ssh_provision"] = {
            "hostname": "base-vm",
            "user": "vmuser",
            "ssh_host_port": 2222,
            "copy_from_host": [{"source": "vms/profile-files/script", "dest": "/home/vmuser/bin/script", "dest_mode": "755"}],
            "post_install_run": ["~/bin/script"],
        }
        self.write_config_dir()

        local_vm = {
            "ssh_provision": {
                "copy_from_host": [{"source": "~/.config/app/", "dest": "/home/vmuser/.config/app"}],
            },
        }
        self.write_extra_profile("local.json", {"vms": {self.vm_name: local_vm}})

        config = self.vmctl.load_config()

        vm = config["vms"][self.vm_name]
        copies = vm["ssh_provision"]["copy_from_host"]
        self.assertEqual(len(copies), 2)
        self.assertEqual(copies[0]["source"], "vms/profile-files/script")
        self.assertEqual(copies[1]["source"], "~/.config/app/")
        self.assertEqual(vm["ssh_provision"]["post_install_run"], ["~/bin/script"])

    def test_load_config_rejects_duplicate_shared_profile(self):
        duplicate_vm = json.loads(json.dumps(self.vm_config))
        self.write_extra_profile("z-duplicate.json", {"vms": {self.vm_name: duplicate_vm}})

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("Duplicate VM profile", str(ctx.exception))

    def test_load_config_rejects_duplicate_ssh_host_port_across_vms(self):
        other_vm = json.loads(json.dumps(self.vm_config))
        other_vm["disk"]["path"] = "artifacts/othervm/disk.qcow2"
        self.vm_config["cloud_init"] = {"user": "tester", "ssh_host_port": 2222}
        other_vm["cloud_init"] = {"user": "tester2", "ssh_host_port": 2222}
        self.write_config_dir()
        self.write_extra_profile("other.json", {"vms": {"othervm": other_vm}})

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("Duplicate ssh_host_port 2222", str(ctx.exception))

    def test_load_config_rejects_autoinstall_placeholder_password_hash(self):
        self.vm_config["autoinstall"] = {
            "hostname": "testvm",
            "username": "vmuser",
            "password_hash": "REPLACE_WITH_SHA512_HASH",
        }
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("autoinstall.password_hash still uses the placeholder value", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


class UserPlaceholderTests(BaseVmctlTestCase):
    def test_user_placeholder_is_expanded_from_ssh_provision_user(self):
        self.vm_config["ssh_provision"] = {
            "user": "alice",
            "copy_from_host": [{"source": "x", "dest": "/home/{{user}}/bin/x"}],
            "post_install_run": ["chown {{user}}:{{user}} /home/{{user}}"],
        }
        self.write_config_dir()

        vm = self.vmctl.load_config()["vms"][self.vm_name]

        self.assertEqual(vm["ssh_provision"]["copy_from_host"][0]["dest"], "/home/alice/bin/x")
        self.assertEqual(vm["ssh_provision"]["post_install_run"], ["chown alice:alice /home/alice"])

    def test_local_override_of_user_propagates_to_placeholders(self):
        self.vm_config["ssh_provision"] = {"user": "lab", "post_install_run": ["id {{user}}"]}
        self.write_config_dir()
        self.write_extra_profile("local.json", {"vms": {self.vm_name: {"ssh_provision": {"user": "bob"}}}})

        vm = self.vmctl.load_config()["vms"][self.vm_name]

        self.assertEqual(vm["ssh_provision"]["user"], "bob")
        self.assertEqual(vm["ssh_provision"]["post_install_run"], ["id bob"])

    def test_placeholder_without_declared_user_is_rejected(self):
        self.vm_config["autoinstall"] = {"password_hash": "$6$hash", "late_commands": ["touch /home/{{user}}/ok"]}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("declares no guest user", str(ctx.exception))

    def test_disagreeing_user_fields_are_rejected(self):
        self.vm_config["ssh_provision"] = {"user": "alice"}
        self.vm_config["autoinstall"] = {"username": "bob", "password_hash": "$6$hash"}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("guest user fields disagree", str(ctx.exception))

    def test_placeholder_inside_identity_field_is_rejected(self):
        self.vm_config["ssh_provision"] = {"user": "{{user}}"}
        self.write_config_dir()

        with self.assertRaises(self.vmctl.VMError) as ctx:
            self.vmctl.load_config()

        self.assertIn("cannot itself contain", str(ctx.exception))


class NaturalOrderTests(unittest.TestCase):
    def test_natural_key_orders_embedded_numbers_by_value(self):
        import vmctl.config
        names = ["ubuntu-10.04-desktop", "ubuntu-8.04-desktop", "ubuntu-24.04-desktop", "reactos", "ubuntu-8.04-unattended", "Windows7-unattended", "windows10-unattended"]
        ordered = sorted(names, key=vmctl.config.natural_key)
        self.assertEqual(ordered, ["reactos", "ubuntu-8.04-desktop", "ubuntu-8.04-unattended", "ubuntu-10.04-desktop", "ubuntu-24.04-desktop", "Windows7-unattended", "windows10-unattended"])
        cfg = {"vms": {n: {"i": i} for i, n in enumerate(names)}}
        self.assertEqual(vmctl.config.sorted_vm_names(cfg), ordered)
        self.assertEqual([n for n, _ in vmctl.config.sorted_vm_items(cfg)], ordered)
