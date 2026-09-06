import argparse
import base64
import json
import shutil
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.errors  # noqa: E402
import vmctl.lifecycle  # noqa: E402
import vmctl.netlab  # noqa: E402
import vmctl.pfsense  # noqa: E402
import vmctl.qemu  # noqa: E402
import vmctl.runtime  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


def lab_profiles(root: Path) -> dict:
    """A three-VM lab (router, Pi-hole, client) with generic identities, like the tracked profiles."""
    def base(name: str, **extra):
        vm = {
            "name": name, "iso": f"isos/{name}.iso",
            "disk": {"path": f"artifacts/{name}/disk.qcow2", "size": "8G", "format": "qcow2", "interface": "virtio"},
            "firmware": {"type": "bios"}, "machine": "pc", "memory_mb": 1024, "cpus": 1, "network": "user",
            "video": {"default": "std", "variants": {"std": ["-vga", "std"]}},
        }
        vm.update(extra)
        return vm

    return {
        "router": base(
            "router",
            networks=[{"id": "wan", "type": "user", "hostfwd": [{"host_port": 8080, "guest_port": 80}, {"host_port": 8081, "guest_port": 8081},
                                                             {"host_port": 2238, "guest_port": 2238}, {"host_port": 2239, "guest_port": 2239}]},
                      {"id": "lan", "type": "segment", "name": "lab-lan"}],
            ssh_provision={"user": "lab", "ssh_host_port": 2237},
            pfsense_config={"username": "lab", "password": "s3cret&<pw>", "timezone": "Europe/Rome"},
            network_lab={"role": "pfsense", "hostname": "firewall", "ip": "192.168.0.1",
                         "lan": {"name": "lab-lan", "subnet": "192.168.0.0/24", "host_ip": "192.168.0.254", "bridge": "virbr-lab", "domain": "qlan"},
                         "dhcp": {"enabled": True, "start": "192.168.0.150", "end": "192.168.0.199", "lease_hours": 12}},
        ),
        "dns": base(
            "dns", firmware={"type": "efi", "code": "/x/CODE.fd", "vars_template": "/x/VARS.fd", "vars_path": "artifacts/dns/VARS.fd"}, machine="q35",
            iso_url="https://example.invalid/ubuntu.iso",
            networks=[{"id": "install", "type": "user", "phase": "install"}, {"id": "lan", "type": "segment", "name": "lab-lan", "phase": "runtime"}],
            autoinstall={"username": "lab", "password_hash": "$6$x"},
            ssh_provision={"user": "lab", "ssh_host_port": 2238},
            network_lab={"role": "pihole", "gateway_vm": "router", "hostname": "pi-hole", "ip": "192.168.0.10", "web_host_port": 8081,
                         "password": "webpw", "upstream_dns": ["9.9.9.9"], "hosts": ["192.168.0.50 nas.qlan"], "cnames": ["pfsense.qlan,firewall.qlan"]},
        ),
        "desk": base(
            "desk", machine="q35", iso_url="https://example.invalid/ubuntu.iso",
            networks=[{"id": "install", "type": "user", "phase": "install"}, {"id": "lan", "type": "segment", "name": "lab-lan", "phase": "runtime"}],
            autoinstall={"username": "lab", "password_hash": "$6$x"},
            ssh_provision={"user": "lab", "ssh_host_port": 2239},
            network_lab={"role": "client", "gateway_vm": "router", "hostname": "lubuntu", "ip": "192.168.0.100"},
        ),
    }


class PfsenseLabBase(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.lab = lab_profiles(self.root)
        self.write_extra_profile("lab.json", {"vms": self.lab})
        self.cfg = self.vmctl.load_config()
        self.top = vmctl.netlab.topology(self.cfg, "router")


class PfsenseRenderTests(PfsenseLabBase):
    def test_config_xml_carries_users_interfaces_nat_and_wan_admin_rules(self):
        xml_text = vmctl.pfsense.render_config_xml("router", self.lab["router"], self.top, password_hash="$2b$12$fakehash",
                                                   authorized_keys=["ssh-ed25519 AAAATEST lab@host"])
        root = ET.fromstring(xml_text)
        self.assertEqual(root.findtext("version"), vmctl.pfsense.CONFIG_VERSION)
        system = root.find("system")
        self.assertEqual(system.findtext("hostname"), "firewall")
        self.assertEqual(system.findtext("domain"), "qlan")
        self.assertEqual(system.findtext("dnsserver"), "192.168.0.10")
        self.assertEqual(system.findtext("timezone"), "Europe/Rome")
        users = {u.findtext("name"): u for u in system.findall("user")}
        self.assertEqual(set(users), {"admin", "lab"})
        for user in users.values():
            self.assertEqual(user.findtext("bcrypt-hash"), "$2b$12$fakehash")
            self.assertEqual(base64.b64decode(user.findtext("authorizedkeys")).decode(), "ssh-ed25519 AAAATEST lab@host")
        self.assertNotIn("s3cret", xml_text)  # the plain password never lands in the config
        self.assertEqual(users["lab"].findtext("uid"), "2000")
        self.assertIn("2000", [m.text for m in system.find("group[name='admins']").findall("member")])
        self.assertEqual(system.find("ssh").findtext("enable"), "enabled")
        for flag in ("disablechecksumoffloading", "enableserial"):
            self.assertIsNotNone(system.find(flag), flag)
        self.assertEqual(root.findtext("interfaces/wan/if"), "vtnet0")
        self.assertEqual(root.findtext("interfaces/wan/ipaddr"), "dhcp")
        self.assertIsNone(root.find("interfaces/wan/blockpriv"))  # the WAN is a private virtual network: never block it
        self.assertEqual(root.findtext("interfaces/lan/if"), "vtnet1")
        self.assertEqual(root.findtext("interfaces/lan/ipaddr"), "192.168.0.1")
        self.assertEqual(root.findtext("interfaces/lan/subnet"), "24")
        self.assertIsNone(root.find("dhcpd/lan/enable"))  # DHCP is Pi-hole's job
        self.assertIsNotNone(root.find("unbound/enable"))
        self.assertEqual(root.findtext("nat/outbound/mode"), "automatic")
        forwards = {(r.findtext("destination/port"), r.findtext("target"), r.findtext("local-port")) for r in root.findall("nat/rule")}
        self.assertEqual(forwards, {("2238", "192.168.0.10", "22"), ("2239", "192.168.0.100", "22"), ("8081", "192.168.0.10", "80")})
        for rule in root.findall("nat/rule"):
            self.assertEqual(rule.findtext("interface"), "wan")
            self.assertEqual(rule.findtext("associated-rule-id"), "pass")
        wan_rules = [r for r in root.findall("filter/rule") if r.findtext("interface") == "wan"]
        self.assertEqual({r.findtext("destination/port") for r in wan_rules}, {"80", "22"})
        self.assertTrue(all(r.findtext("type") == "pass" for r in wan_rules))
        lan_rules = [r for r in root.findall("filter/rule") if r.findtext("interface") == "lan"]
        self.assertEqual({r.findtext("ipprotocol") for r in lan_rules}, {"inet", "inet6"})

    def test_config_xml_without_keys_and_with_dns_falling_back_to_the_router(self):
        del self.lab["dns"]
        self.write_extra_profile("lab.json", {"vms": self.lab})
        cfg = self.vmctl.load_config()
        top = vmctl.netlab.topology(cfg, "router")
        root = ET.fromstring(vmctl.pfsense.render_config_xml("router", self.lab["router"], top, password_hash="h"))
        self.assertEqual(root.findtext("system/dnsserver"), "192.168.0.1")
        self.assertIsNone(root.find("system/user/authorizedkeys"))
        self.assertEqual([r.findtext("target") for r in root.findall("nat/rule")], ["192.168.0.100"])

    def test_identity_rules(self):
        for bad in ({"username": "lab"}, {"password": "x"}, {"username": "admin", "password": "x"}):
            self.lab["router"]["pfsense_config"] = bad
            with self.subTest(bad=bad), self.assertRaises(vmctl.errors.VMError):
                vmctl.pfsense.render_config_xml("router", self.lab["router"], self.top, password_hash="h")

    def test_bcrypt_is_used_when_no_hash_is_given(self):
        fake = mock.MagicMock()
        fake.hashpw.return_value = b"$2b$12$generated"
        fake.gensalt.return_value = b"salt"
        with mock.patch.dict(sys.modules, {"bcrypt": fake}):
            xml_text = vmctl.pfsense.render_config_xml("router", self.lab["router"], self.top)
        self.assertIn("$2b$12$generated", xml_text)
        fake.hashpw.assert_called_once_with(b"s3cret&<pw>", b"salt")
        with mock.patch.dict(sys.modules, {"bcrypt": None}), self.assertRaises(vmctl.errors.VMError) as ctx:
            vmctl.pfsense.bcrypt_hash("x")
        self.assertIn("python3-bcrypt", str(ctx.exception))

    def test_rc_local_flushes_then_prints_the_token_then_powers_off(self):
        rc = vmctl.pfsense.render_rc_local()
        ok_branch = rc.split("if bsdinstall script /etc/installerconfig", 1)[1].split("else", 1)[0]
        self.assertLess(ok_branch.index("sync"), ok_branch.index(vmctl.pfsense.BOOTSTRAP_COMPLETE_TOKEN))
        self.assertLess(ok_branch.index(vmctl.pfsense.BOOTSTRAP_COMPLETE_TOKEN), ok_branch.index("shutdown -p now"))
        self.assertIn("> /dev/cuau0", ok_branch)  # COM1 = the host's serial stdio
        failed_branch = rc.split("else", 1)[1]
        self.assertIn(vmctl.pfsense.BOOTSTRAP_FAILED_TOKEN, failed_branch)
        self.assertIn("bsdinstall_log", failed_branch)
        self.assertIn("shutdown -p now", failed_branch)  # the host must not wait for the timeout

    def test_installerconfig_embeds_the_config_and_targets_bios_zfs_on_vtbd0(self):
        text = vmctl.pfsense.render_installerconfig("<pfsense>\n  <x/>\n</pfsense>\n")
        self.assertIn('export ZFSBOOT_DISKS="vtbd0"', text)
        self.assertIn('export ZFSBOOT_BOOT_TYPE="BIOS"', text)
        self.assertIn('export nonInteractive="YES"', text)
        self.assertIn("cat > /cf/conf/config.xml <<'VMCTL_PFSENSE_CONFIG'\n<pfsense>\n  <x/>\n</pfsense>\nVMCTL_PFSENSE_CONFIG\n", text)
        self.assertLess(text.index("chmod 600 /cf/conf/config.xml"), text.index("sync"))
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.pfsense.render_installerconfig("VMCTL_PFSENSE_CONFIG")

    def test_bsdinstall_patch_is_exact_and_refuses_other_versions(self):
        script = "prelude\nbsdinstall umount\nif [ \"$ZFSBOOT_DISKS\" ]; then\n  zpool export\nfi\n"
        patched = vmctl.pfsense.patch_bsdinstall_script(script)
        self.assertIn('bsdinstall umount || [ -n "$ZFSBOOT_DISKS" ]\nif [ "$ZFSBOOT_DISKS" ]; then', patched)
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.pfsense.patch_bsdinstall_script("bsdinstall umount\nsomething else\n")

    def test_install_media_is_an_ide_cd_after_the_disk_in_the_boot_order(self):
        args = vmctl.pfsense.install_media_args(Path("/x/install.iso"))
        self.assertEqual(args[1], "id=pfcd0,file=/x/install.iso,format=raw,if=none,media=cdrom,readonly=on")
        self.assertEqual(args[3], "ide-cd,drive=pfcd0,bus=ide.1,bootindex=2")

    def test_profile_checks(self):
        vm = json.loads(json.dumps(self.lab["router"]))
        vmctl.pfsense.check_profile("router", vm)
        for mutate in (
            lambda v: v.__setitem__("firmware", {"type": "efi", "code": "c", "vars_template": "t", "vars_path": "p"}),
            lambda v: v["disk"].__setitem__("interface", "sata"),
            lambda v: v["disk"].__setitem__("format", "vhd"),
            lambda v: v.__setitem__("networks", list(reversed(v["networks"]))),
            lambda v: v["networks"][1].__setitem__("phase", "runtime"),
            lambda v: v.__setitem__("network_lab", {"role": "client", "gateway_vm": "router", "ip": "192.168.0.2"}),
        ):
            broken = json.loads(json.dumps(self.lab["router"]))
            mutate(broken)
            with self.subTest(), self.assertRaises(vmctl.errors.VMError):
                vmctl.pfsense.check_profile("router", broken)


class PfsenseIsoBuildTests(PfsenseLabBase):
    def _source_iso(self) -> Path:
        iso = self.root / "isos/router.iso"
        iso.parent.mkdir(parents=True, exist_ok=True)
        iso.write_bytes(b"ISO" * 100)
        return iso

    def test_ensure_install_iso_extracts_patches_and_grafts_with_growisofs(self):
        source = self._source_iso()
        vm = self.lab["router"]
        work = vmctl.pfsense.pfsense_artifact_dir(vm) / "iso-work"

        def fake_run(cmd, **kwargs):
            if cmd[0] == "xorriso":
                (work / "rc.original").write_text("...\nbsdinstall script /etc/installerconfig\n", encoding="utf-8")
                (work / "bsdinstall-script").write_text('x\nbsdinstall umount\nif [ "$ZFSBOOT_DISKS" ]; then\nfi\n', encoding="utf-8")
            elif cmd[0] == "cp":
                Path(cmd[2]).write_bytes(Path(cmd[1]).read_bytes())
            elif cmd[0] == "growisofs":
                Path(cmd[2]).write_bytes(Path(cmd[2]).read_bytes() + b"GRAFTED")

        with mock.patch.object(shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run:
            dest = vmctl.pfsense.ensure_install_iso("router", vm, source, "<pfsense/>\n")
        commands = [c.args[0] for c in run.call_args_list]
        self.assertEqual([c[0] for c in commands], ["xorriso", "cp", "growisofs"])
        self.assertIn("-osirrox", commands[0])
        growisofs = commands[2]
        self.assertEqual(growisofs[1:3], ["-M", str(dest) + ".part"])
        self.assertTrue(any(a.startswith("/etc/installerconfig=") for a in growisofs))
        self.assertTrue(any(a.startswith("/etc/rc.local=") for a in growisofs))
        self.assertTrue(any(a.startswith("/usr/libexec/bsdinstall/script=") for a in growisofs))
        self.assertEqual(dest, self.root / "artifacts/router/pfsense/install.iso")
        self.assertTrue(dest.read_bytes().endswith(b"GRAFTED"))
        self.assertFalse(work.exists())  # scratch removed
        stamp = dest.with_name("install.iso.source")
        self.assertNotIn("<pfsense/>", stamp.read_text(encoding="utf-8"))  # the stamp records a hash of the config, not the XML
        self.assertTrue(stamp.is_file())

        # Cached: same source + same config -> nothing runs; a config change -> rebuild.
        with mock.patch.object(shutil, "which", return_value="/usr/bin/tool"), mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run:
            vmctl.pfsense.ensure_install_iso("router", vm, source, "<pfsense/>\n")
        run.assert_not_called()
        with mock.patch.object(shutil, "which", return_value="/usr/bin/tool"), mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run:
            vmctl.pfsense.ensure_install_iso("router", vm, source, "<pfsense><changed/></pfsense>\n")
        self.assertEqual(len(run.call_args_list), 3)

    def test_ensure_install_iso_rejects_a_foreign_iso(self):
        source = self._source_iso()
        vm = self.lab["router"]
        work = vmctl.pfsense.pfsense_artifact_dir(vm) / "iso-work"

        def fake_run(cmd, **kwargs):
            if cmd[0] == "xorriso":
                (work / "rc.original").write_text("no installer here\n", encoding="utf-8")
                (work / "bsdinstall-script").write_text("x\n", encoding="utf-8")

        with mock.patch.object(shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(vmctl.runtime, "run", side_effect=fake_run), \
             self.assertRaises(vmctl.errors.VMError) as ctx:
            vmctl.pfsense.ensure_install_iso("router", vm, source, "<pfsense/>\n")
        self.assertIn("pfSense CE 2.7.2", str(ctx.exception))

    def test_dry_run_only_prints_the_commands(self):
        with mock.patch.object(shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(vmctl.runtime, "run") as run:
            dest = vmctl.pfsense.ensure_install_iso("router", self.lab["router"], self.root / "isos/missing.iso", "<pfsense/>\n", dry_run=True)
        self.assertEqual([c.args[0][0] for c in run.call_args_list], ["xorriso", "cp", "growisofs"])
        self.assertTrue(all(c.kwargs.get("dry_run") for c in run.call_args_list))
        self.assertFalse(dest.exists())


class PfsenseBootstrapTests(PfsenseLabBase):
    def test_cmd_bootstrap_pfsense_builds_the_iso_and_waits_for_the_token(self):
        iso = self.root / "isos/router.iso"
        iso.parent.mkdir(parents=True)
        iso.write_bytes(b"x")
        args = argparse.Namespace(vm="router", timeout=900, dry_run=False)
        with mock.patch.object(vmctl.lifecycle, "ensure_vm_disk"), \
             mock.patch.object(vmctl.pfsense, "render_config_xml", return_value="<pfsense/>") as render, \
             mock.patch.object(vmctl.pfsense, "ensure_install_iso", return_value=self.root / "artifacts/router/pfsense/install.iso") as build, \
             mock.patch.object(vmctl.qemu, "common_args", return_value=["qemu-system-x86_64"]) as common_args, \
             mock.patch.object(vmctl.qemu, "run_and_expect") as run_and_expect, \
             mock.patch.object(vmctl.cloud_init, "_authorized_keys_for_vm", return_value=["ssh-ed25519 K"]):
            self.assertEqual(self.vmctl.cmd_bootstrap_pfsense(args), 0)
        self.assertEqual(render.call_args.kwargs["authorized_keys"], ["ssh-ed25519 K"])
        self.assertIsNone(render.call_args.kwargs["password_hash"])
        build.assert_called_once()
        self.assertEqual(build.call_args.args[2], iso)
        kwargs = common_args.call_args.kwargs
        self.assertTrue(kwargs["serial_stdio"] and kwargs["headless"] and kwargs["no_reboot"])
        self.assertEqual(kwargs["disk_bootindex"], 1)
        self.assertEqual(kwargs["network_phase"], "install")
        cmd = run_and_expect.call_args.args[0]
        self.assertIn("ide-cd,drive=pfcd0,bus=ide.1,bootindex=2", cmd)
        self.assertEqual(run_and_expect.call_args.kwargs["expected_text"], vmctl.pfsense.BOOTSTRAP_COMPLETE_TOKEN)
        self.assertEqual(run_and_expect.call_args.kwargs["timeout_sec"], 900)
        self.assertEqual(run_and_expect.call_args.kwargs["exit_grace_sec"], vmctl.pfsense.SHUTDOWN_GRACE_SEC)

    def test_cmd_bootstrap_pfsense_explains_a_missing_iso_and_tolerates_it_in_dry_run(self):
        args = argparse.Namespace(vm="router", timeout=900, dry_run=False)
        with self.assertRaises(vmctl.errors.VMError) as ctx:
            self.vmctl.cmd_bootstrap_pfsense(args)
        self.assertIn("local.json", str(ctx.exception))
        args = argparse.Namespace(vm="router", timeout=900, dry_run=True)
        with mock.patch.object(shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(vmctl.runtime, "run") as run, \
             mock.patch.object(vmctl.qemu, "run_and_expect") as run_and_expect, \
             mock.patch.object(vmctl.cloud_init, "_authorized_keys_for_vm", return_value=[]):
            self.assertEqual(self.vmctl.cmd_bootstrap_pfsense(args), 0)
        self.assertTrue(all(c.kwargs.get("dry_run") for c in run.call_args_list))
        self.assertTrue(run_and_expect.call_args.kwargs["dry_run"])

    def test_cmd_bootstrap_pfsense_reports_the_failure_token(self):
        (self.root / "isos").mkdir()
        (self.root / "isos/router.iso").write_bytes(b"x")
        args = argparse.Namespace(vm="router", timeout=900, dry_run=False)
        failure = vmctl.errors.VMError(f"Timed out\n{vmctl.pfsense.BOOTSTRAP_FAILED_TOKEN}\nlog line")
        with mock.patch.object(vmctl.lifecycle, "ensure_vm_disk"), \
             mock.patch.object(vmctl.pfsense, "render_config_xml", return_value="<pfsense/>"), \
             mock.patch.object(vmctl.pfsense, "ensure_install_iso", return_value=self.root / "i.iso"), \
             mock.patch.object(vmctl.qemu, "common_args", return_value=["qemu-system-x86_64"]), \
             mock.patch.object(vmctl.qemu, "run_and_expect", side_effect=failure), \
             mock.patch.object(vmctl.cloud_init, "_authorized_keys_for_vm", return_value=[]), \
             self.assertRaises(vmctl.errors.VMError) as ctx:
            self.vmctl.cmd_bootstrap_pfsense(args)
        self.assertIn("bsdinstall reported a failure", str(ctx.exception))

    def test_requires_pfsense_config_and_dispatches_in_check_vms(self):
        with self.assertRaises(vmctl.errors.VMError):
            self.vmctl.cmd_bootstrap_pfsense(argparse.Namespace(vm=self.vm_name, timeout=1, dry_run=True))
        self.assertEqual(self.vmctl.local_test_mode(self.lab["router"])[0], "bootstrap-pfsense")
        self.assertEqual(self.vmctl.local_test_mode(self.lab["dns"])[0], "bootstrap-unattended")


if __name__ == "__main__":
    unittest.main()
