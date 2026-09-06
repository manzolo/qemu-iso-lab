import argparse
import contextlib
import io
import json
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.errors  # noqa: E402
import vmctl.libvirt  # noqa: E402
import vmctl.lifecycle  # noqa: E402
import vmctl.netlab  # noqa: E402
import vmctl.qemu  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.ssh  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402
from tests.test_pfsense import lab_profiles  # noqa: E402


class NetlabBase(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.lab = lab_profiles(self.root)
        self.write_extra_profile("lab.json", {"vms": self.lab})
        self.cfg = self.vmctl.load_config()

    def reload(self):
        self.write_extra_profile("lab.json", {"vms": self.lab})
        self.cfg = self.vmctl.load_config()
        return self.cfg


class TopologyTests(NetlabBase):
    def test_topology_resolves_from_any_member(self):
        for name in ("router", "dns", "desk"):
            top = vmctl.netlab.topology(self.cfg, name)
            self.assertEqual(top["router"], "router")
        top = vmctl.netlab.topology(self.cfg, "desk")
        self.assertEqual(top["router_ip"], "192.168.0.1")
        self.assertEqual(top["dns_ip"], "192.168.0.10")
        self.assertEqual(top["lan"], {"name": "lab-lan", "subnet": "192.168.0.0/24", "prefix": 24, "netmask": "255.255.255.0",
                                      "host_ip": "192.168.0.254", "bridge": "virbr-lab", "domain": "qlan"})
        self.assertEqual(top["dhcp"], {"enabled": True, "start": "192.168.0.150", "end": "192.168.0.199", "lease_hours": 12})
        self.assertEqual([(m["name"], m["role"], m["ip"], m["ssh_port"]) for m in top["members"]],
                         [("desk", "client", "192.168.0.100", 2239), ("dns", "pihole", "192.168.0.10", 2238)])
        self.assertEqual({(f["wan_port"], f["target"], f["target_port"]) for f in top["forwards"]},
                         {(2239, "192.168.0.100", 22), (2238, "192.168.0.10", 22), (8081, "192.168.0.10", 80)})
        self.assertEqual(top["router_gui"], {8080: 80})
        self.assertEqual(top["router_ssh_port"], 2237)
        self.assertEqual(vmctl.netlab.lab_vm_names(self.cfg, "router"), ["router", "dns", "desk"])
        self.assertEqual(vmctl.netlab.routers(self.cfg), ["router"])

    def test_vm_outside_the_lab_and_bad_roles(self):
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.netlab.topology(self.cfg, self.vm_name)
        self.assertIsNone(vmctl.netlab.lab_config(self.vm_config))
        self.vm_config["network_lab"] = {"role": "switch"}
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.netlab.lab_config(self.vm_config)

    def test_validation_catches_topology_mistakes(self):
        cases = {
            "duplicate address": lambda: self.lab["desk"]["network_lab"].__setitem__("ip", "192.168.0.10"),
            "outside subnet": lambda: self.lab["desk"]["network_lab"].__setitem__("ip", "10.0.0.5"),
            "broadcast": lambda: self.lab["desk"]["network_lab"].__setitem__("ip", "192.168.0.255"),
            "pool overlaps static": lambda: self.lab["router"]["network_lab"]["dhcp"].update(start="192.168.0.90", end="192.168.0.120"),
            "inverted pool": lambda: self.lab["router"]["network_lab"]["dhcp"].update(start="192.168.0.199", end="192.168.0.150"),
            "unknown gateway": lambda: self.lab["desk"]["network_lab"].__setitem__("gateway_vm", "nope"),
            "missing gateway": lambda: self.lab["desk"]["network_lab"].pop("gateway_vm"),
            "no lan on router": lambda: self.lab["router"]["network_lab"].pop("lan"),
            "bad subnet": lambda: self.lab["router"]["network_lab"]["lan"].__setitem__("subnet", "192.168.0.1/24"),
            "two piholes": lambda: self.lab["desk"]["network_lab"].__setitem__("role", "pihole"),
            "missing hostfwd on router": lambda: self.lab["router"]["networks"][0].__setitem__("hostfwd", [{"host_port": 8080, "guest_port": 80}]),
            "hostfwd to another guest port": lambda: self.lab["router"]["networks"][0]["hostfwd"].__setitem__(2, {"host_port": 2238, "guest_port": 22}),
        }
        for label, mutate in cases.items():
            self.lab = lab_profiles(self.root)
            mutate()
            cfg = self.reload()
            with self.subTest(label), self.assertRaises(vmctl.errors.VMError):
                vmctl.netlab.topology(cfg, "router")

    def test_segment_network_xml_is_a_plain_bridge_with_the_host_address(self):
        root = ET.fromstring(vmctl.netlab.segment_network_xml(vmctl.netlab.topology(self.cfg, "router")))
        self.assertEqual(root.findtext("name"), "lab-lan")
        self.assertEqual(root.find("bridge").get("name"), "virbr-lab")
        self.assertEqual(root.find("dns").get("enable"), "no")
        self.assertEqual(root.find("ip").attrib, {"address": "192.168.0.254", "prefix": "24"})
        self.assertIsNone(root.find("forward"))
        self.assertIsNone(root.find(".//dhcp"))


class GuestRenderTests(NetlabBase):
    def test_netplan_for_pihole_and_client(self):
        top = vmctl.netlab.topology(self.cfg, "router")
        pihole = vmctl.netlab.netplan_yaml(top, vmctl.netlab.member_of(top, "dns"), "52:54:00:aa:bb:cc", "enp0s2")
        self.assertIn("renderer: networkd", pihole)
        self.assertIn('macaddress: "52:54:00:aa:bb:cc"', pihole)
        self.assertIn("set-name: enp0s2", pihole)
        self.assertIn("- 192.168.0.10/24", pihole)
        self.assertIn("via: 192.168.0.1", pihole)
        self.assertIn("- 192.168.0.10\n", pihole)  # nameserver = itself
        self.assertIn("- qlan", pihole)
        client = vmctl.netlab.netplan_yaml(top, vmctl.netlab.member_of(top, "desk"), "52:54:00:aa:bb:cd", "enp0s2")
        self.assertIn("renderer: NetworkManager", client)
        self.assertIn("- 192.168.0.100/24", client)
        self.assertIn("dhcp4: false", client)

    def test_pihole_toml_has_upstreams_records_and_an_inactive_dhcp_pool(self):
        top = vmctl.netlab.topology(self.cfg, "router")
        toml = vmctl.netlab.pihole_toml(top, vmctl.netlab.member_of(top, "dns"), "enp0s2")
        self.assertIn('upstreams = ["9.9.9.9"]', toml)
        self.assertIn('"192.168.0.50 nas.qlan"', toml)
        self.assertIn('"192.168.0.1 firewall.qlan"', toml)  # the router record is always added
        self.assertIn('"192.168.0.100 lubuntu.qlan"', toml)  # and every member
        self.assertIn('cnameRecords = ["pfsense.qlan,firewall.qlan"]', toml)
        self.assertIn('interface = "enp0s2"', toml)
        self.assertIn("active = false", toml)
        self.assertIn('start = "192.168.0.150"', toml)
        self.assertIn('router = "192.168.0.1"', toml)
        self.assertIn('netmask = "255.255.255.0"', toml)
        self.assertIn('leaseTime = "12h"', toml)

    def test_setup_script_per_role(self):
        top = vmctl.netlab.topology(self.cfg, "router")
        pihole = vmctl.netlab.guest_setup_script(top, vmctl.netlab.member_of(top, "dns"))
        self.assertIn("basic-install.sh --unattended", pihole)
        self.assertIn("pihole setpassword", pihole)
        self.assertIn(f"install -m 0600 lan.yaml {vmctl.netlab.NETPLAN_FILE}", pihole)
        self.assertIn("network: {config: disabled}", pihole)
        self.assertIn("pihole-FTL --config dhcp.active true", pihole)
        self.assertNotIn("wait-online", pihole)
        self.assertLess(pihole.index("basic-install.sh"), pihole.index("lan.yaml"))  # install first, LAN address last
        client = vmctl.netlab.guest_setup_script(top, vmctl.netlab.member_of(top, "desk"))
        self.assertNotIn("basic-install", client)
        self.assertIn("systemctl disable systemd-networkd-wait-online.service systemd-networkd.service systemd-networkd.socket", client)
        self.lab["router"]["network_lab"]["dhcp"]["enabled"] = False
        top = vmctl.netlab.topology(self.reload(), "router")
        self.assertIn("dhcp.active false", vmctl.netlab.guest_setup_script(top, vmctl.netlab.member_of(top, "dns")))

    def test_provision_guest_stages_files_and_runs_the_script_over_ssh(self):
        vm = self.lab["dns"]
        mac = vmctl.netlab.runtime_mac(vm)
        with mock.patch.object(vmctl.runtime, "run_output", return_value="enp0s2\n") as run_output, \
             mock.patch.object(vmctl.ssh, "post_install_run") as run, \
             mock.patch.object(vmctl.ssh, "post_install_copy_raw") as copy:
            vmctl.netlab.provision_guest("dns", vm)
        self.assertIn(mac, run_output.call_args.args[0][-1])
        staged = self.root / "artifacts/dns/netlab"
        self.assertEqual(sorted(p.name for p in staged.iterdir()), ["adlists.list", "lan.yaml", "pihole.toml", "setup.sh", "web-password"])
        self.assertEqual((staged / "web-password").read_text(), "webpw\n")
        self.assertEqual(oct((staged / "web-password").stat().st_mode & 0o777), "0o600")
        self.assertIn('interface = "enp0s2"', (staged / "pihole.toml").read_text())
        self.assertEqual(copy.call_args.args[1], {"source": str(staged), "dest": "/tmp/vmctl-netlab"})
        commands = [c.args[1] for c in run.call_args_list]
        self.assertEqual(commands, ["rm -rf /tmp/vmctl-netlab", "sudo bash /tmp/vmctl-netlab/setup.sh", "rm -rf /tmp/vmctl-netlab"])

    def test_provision_guest_is_a_no_op_outside_the_lab_and_for_the_router(self):
        with mock.patch.object(vmctl.ssh, "post_install_run") as run:
            vmctl.netlab.provision_guest(self.vm_name, self.vm_config)
            vmctl.netlab.provision_guest("router", self.lab["router"])
        run.assert_not_called()

    def test_run_post_install_calls_the_hook(self):
        vm = self.lab["dns"]
        with mock.patch("shutil.which", return_value="/usr/bin/ssh"), \
             mock.patch.object(vmctl.ssh, "wait_for_ssh"), \
             mock.patch.object(vmctl.ssh, "ensure_passwordless_sudo"), \
             mock.patch.object(vmctl.ssh, "wait_for_guest_post_install_ready"), \
             mock.patch.object(vmctl.ssh, "provision_shared_dir"), \
             mock.patch.object(vmctl.netlab, "provision_guest") as hook, \
             mock.patch.object(vmctl.ssh, "post_install_run"):
            self.vmctl.run_post_install("dns", vm, 30)
        hook.assert_called_once()
        self.assertEqual(hook.call_args.args[:2], ("dns", vm))


class LabCommandTests(NetlabBase):
    def run_lab(self, action, vm=None, **extra):
        args = argparse.Namespace(action=action, vm=vm, router=None, apply=False, timeout=60, dry_run=False)
        for key, value in extra.items():
            setattr(args, key, value)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.vmctl.cmd_lab(args)
        return code, out.getvalue()

    def test_plan_prints_topology_and_the_libvirt_network(self):
        code, out = self.run_lab("plan")
        self.assertEqual(code, 0)
        self.assertIn("192.168.0.1", out)
        self.assertIn("<name>lab-lan</name>", out)
        self.assertIn("router -> dns -> desk", out)
        self.assertIn("http://127.0.0.1:8080/", out)

    def test_install_runs_router_then_members_and_refuses_installed_disks(self):
        calls = []
        with mock.patch.object(vmctl.lifecycle, "cmd_bootstrap_pfsense", side_effect=lambda a: calls.append(("pfsense", a.vm)) or 0), \
             mock.patch.object(vmctl.lifecycle, "cmd_bootstrap_unattended", side_effect=lambda a: calls.append(("ubuntu", a.vm)) or 0), \
             mock.patch.object(vmctl.lifecycle, "cmd_stop", side_effect=lambda a: calls.append(("stop", a.vm)) or 0):
            code, _ = self.run_lab("install")
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("pfsense", "router"), ("ubuntu", "dns"), ("stop", "dns"), ("ubuntu", "desk"), ("stop", "desk")])

        disk = self.root / "artifacts/dns/disk.qcow2"
        disk.parent.mkdir(parents=True)
        disk.write_bytes(b"x" * (2 * 1024 * 1024))
        with mock.patch.object(vmctl.lifecycle, "cmd_bootstrap_pfsense") as pf, self.assertRaises(vmctl.errors.VMError) as ctx:
            self.run_lab("install")
        pf.assert_not_called()
        self.assertIn("dns", str(ctx.exception))
        self.assertIn("vmctl clean", str(ctx.exception))

    def test_up_and_down_start_and_stop_in_order(self):
        started, stopped = [], []
        with mock.patch.object(vmctl.lifecycle, "cmd_start", side_effect=lambda a: started.append((a.vm, a.headless, a.background)) or 0):
            self.assertEqual(self.run_lab("up")[0], 0)
        self.assertEqual(started, [("router", True, True), ("dns", True, True), ("desk", True, True)])
        with mock.patch.object(vmctl.lifecycle, "cmd_stop", side_effect=lambda a: stopped.append(a.vm) or 0):
            self.assertEqual(self.run_lab("down")[0], 0)
        self.assertEqual(stopped, ["desk", "dns", "router"])

    def test_status_lists_every_member_with_its_address(self):
        code, out = self.run_lab("status")
        self.assertEqual(code, 0)
        for token in ("router", "192.168.0.1", "dns", "192.168.0.10", "desk", "192.168.0.100"):
            self.assertIn(token, out)

    def test_attach_prints_or_writes_the_local_override(self):
        code, out = self.run_lab("attach", vm=self.vm_name)
        self.assertEqual(code, 0)
        snippet = json.loads(out[out.index("{"):])
        self.assertEqual(snippet, {"vms": {self.vm_name: {"networks": [{"type": "segment", "name": "lab-lan"}]}}})
        local = self.config_dir / "profiles" / "local.json"
        local.write_text(json.dumps({"vms": {self.vm_name: {"memory_mb": 2048}}}), encoding="utf-8")
        code, _ = self.run_lab("attach", vm=self.vm_name, apply=True)
        self.assertEqual(code, 0)
        merged = json.loads(local.read_text(encoding="utf-8"))
        self.assertEqual(merged["vms"][self.vm_name], {"memory_mb": 2048, "networks": [{"type": "segment", "name": "lab-lan"}]})
        self.assertTrue(local.with_suffix(".json.bak").is_file())
        cfg = self.vmctl.load_config()
        self.assertEqual([s["type"] for s in vmctl.qemu.network_specs(cfg["vms"][self.vm_name])], ["segment"])
        with self.assertRaises(vmctl.errors.VMError):
            self.run_lab("attach", vm="dns")  # already a member
        with self.assertRaises(vmctl.errors.VMError):
            self.run_lab("attach")  # no vm

    def test_cli_wiring(self):
        import vmctl.cli
        parser = vmctl.cli.build_parser()
        args = parser.parse_args(["lab", "install", "--timeout", "10"])
        self.assertIs(args.func, vmctl.lifecycle.cmd_lab)
        args = parser.parse_args(["lab", "libvirt-test", "--keep", "--connect", "qemu:///session", "--wait", "5"])
        self.assertTrue(args.keep)
        self.assertEqual(args.connect, "qemu:///session")
        args = parser.parse_args(["bootstrap-pfsense", "router"])
        self.assertIs(args.func, vmctl.lifecycle.cmd_bootstrap_pfsense)
        self.assertEqual(args.timeout, 1800)
        with self.assertRaises(SystemExit):
            parser.parse_args(["lab", "explode"])


class LabCheckTests(NetlabBase):
    def test_check_targets_per_mode(self):
        top = vmctl.netlab.topology(self.cfg, "router")
        qemu_targets = vmctl.netlab.check_targets(top, "qemu")
        urls = [t["url"] for t in qemu_targets if t["kind"] == "http"]
        ports = [(t["host"], t["port"]) for t in qemu_targets if t["kind"] == "tcp"]
        self.assertEqual(urls, ["http://127.0.0.1:8080/", "http://127.0.0.1:8081/admin/"])
        self.assertEqual(sorted(ports), [("127.0.0.1", 2237), ("127.0.0.1", 2238), ("127.0.0.1", 2239)])
        lv = vmctl.netlab.check_targets(top, "libvirt")
        self.assertEqual([t["url"] for t in lv if t["kind"] == "http"], ["http://192.168.0.1/", "http://192.168.0.10/admin/"])
        self.assertEqual(sorted((t["host"], t["port"]) for t in lv if t["kind"] == "tcp"),
                         [("192.168.0.1", 22), ("192.168.0.10", 22), ("192.168.0.100", 22)])
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.netlab.check_targets(top, "bridge")

    def test_wait_targets_reports_each_target_once(self):
        targets = [{"label": "gui", "kind": "http", "url": "http://x/"}, {"label": "ssh", "kind": "tcp", "host": "h", "port": 22},
                   {"label": "dead", "kind": "tcp", "host": "h", "port": 23}]
        with mock.patch.object(vmctl.netlab, "probe_http", return_value=200), \
             mock.patch.object(vmctl.netlab, "probe_tcp", side_effect=lambda host, port, timeout=2.0: port == 22), \
             mock.patch.object(vmctl.netlab.time, "sleep"):
            results = vmctl.netlab.wait_targets(targets, timeout_sec=0)
        self.assertEqual([(t["label"], ok, detail) for t, ok, detail in results],
                         [("gui", True, "HTTP 200"), ("ssh", True, "port open"), ("dead", False, "no answer")])
        with mock.patch.object(vmctl.netlab, "probe_http", return_value=503), mock.patch.object(vmctl.netlab.time, "sleep"):
            self.assertEqual(vmctl.netlab.wait_targets(targets[:1], timeout_sec=0)[0][1:], (False, "HTTP 503"))

    def test_lab_check_picks_the_mode_and_fails_on_a_missing_service(self):
        args = argparse.Namespace(action="check", vm=None, router=None, apply=False, timeout=60, dry_run=False, wait=1, connect="qemu:///system", libvirt=False)
        with mock.patch.object(vmctl.lifecycle, "lab_in_libvirt", return_value=False), \
             mock.patch.object(vmctl.netlab, "wait_targets", side_effect=lambda targets, wait: [(t, True, "ok") for t in targets]) as wait:
            self.assertEqual(self.vmctl.cmd_lab(args), 0)
        self.assertEqual(wait.call_args.args[0][0]["url"], "http://127.0.0.1:8080/")
        with mock.patch.object(vmctl.lifecycle, "lab_in_libvirt", return_value=True), \
             mock.patch.object(vmctl.netlab, "wait_targets", side_effect=lambda targets, wait: [(t, t["kind"] == "http", "x") for t in targets]) as wait:
            self.assertEqual(self.vmctl.cmd_lab(args), 1)
        self.assertEqual(wait.call_args.args[0][0]["url"], "http://192.168.0.1/")
        args.dry_run = True
        self.assertEqual(self.vmctl.cmd_lab(args), 0)

    def test_export_and_unexport_round_trip(self):
        calls: list[tuple[str, str]] = []
        outputs = {("list", "--name"): "", ("domstate", "router"): "shut off\n", ("domstate", "dns"): "shut off\n", ("domstate", "desk"): "shut off\n"}
        with mock.patch.object(vmctl.lifecycle, "cmd_stop", side_effect=lambda a: calls.append(("stop", a.vm)) or 0), \
             mock.patch.object(vmctl.lifecycle, "cmd_export_libvirt", side_effect=lambda a: calls.append(("export", a.vm)) or 0) as export, \
             mock.patch.object(vmctl.lifecycle, "cmd_unexport_libvirt", side_effect=lambda a: calls.append(("unexport", a.vm)) or 0), \
             mock.patch.object(vmctl.libvirt, "virsh_output", side_effect=lambda uri, *a: outputs.get(a, "")), \
             mock.patch.object(vmctl.runtime, "run", side_effect=lambda cmd, **kw: calls.append((cmd[3], cmd[4]))), \
             mock.patch.object(vmctl.lifecycle, "lab_check", return_value=0) as check, \
             mock.patch.object(vmctl.lifecycle.time, "sleep"):
            args = argparse.Namespace(action="libvirt-test", vm=None, router=None, apply=False, timeout=60, dry_run=False, wait=5,
                                      connect="qemu:///system", replace=True, keep=False, libvirt=False)
            self.assertEqual(self.vmctl.cmd_lab(args), 0)
        self.assertEqual(calls, [("stop", "desk"), ("stop", "dns"), ("stop", "router"),
                                 ("export", "router"), ("export", "dns"), ("export", "desk"),
                                 ("start", "router"), ("start", "dns"), ("start", "desk"),
                                 ("unexport", "desk"), ("unexport", "dns"), ("unexport", "router")])
        self.assertTrue(export.call_args_list[0].args[0].replace)
        self.assertEqual(check.call_args.args[1], "libvirt")

    def test_unexport_shuts_down_running_domains_and_gives_up_after_the_grace(self):
        state = {"router": "running"}
        outputs = {("list", "--name"): "router\n"}
        with mock.patch.object(vmctl.lifecycle, "cmd_unexport_libvirt", return_value=0) as unexport, \
             mock.patch.object(vmctl.libvirt, "virsh_output", side_effect=lambda uri, *a: outputs.get(a, state["router"] + "\n" if a[0] == "domstate" else "")), \
             mock.patch.object(vmctl.runtime, "run") as run, \
             mock.patch.object(vmctl.lifecycle.time, "sleep", side_effect=lambda s: state.__setitem__("router", "shut off")):
            args = argparse.Namespace(action="unexport", vm=None, router=None, apply=False, timeout=60, dry_run=False, wait=30, connect="qemu:///system")
            self.assertEqual(self.vmctl.cmd_lab(args), 0)
        self.assertEqual(run.call_args.args[0][3:], ["shutdown", "router"])
        self.assertEqual([c.args[0].vm for c in unexport.call_args_list], ["desk", "dns", "router"])
        with mock.patch.object(vmctl.lifecycle, "cmd_unexport_libvirt") as unexport, \
             mock.patch.object(vmctl.libvirt, "virsh_output", side_effect=lambda uri, *a: "router\n" if a[0] == "list" else "running\n"), \
             mock.patch.object(vmctl.runtime, "run"), \
             mock.patch.object(vmctl.lifecycle.time, "sleep"), \
             mock.patch.object(vmctl.lifecycle.time, "monotonic", side_effect=[0, 0, 100, 100, 100, 100]), \
             self.assertRaises(vmctl.errors.VMError):
            self.vmctl.cmd_lab(argparse.Namespace(action="unexport", vm=None, router=None, apply=False, timeout=60, dry_run=False, wait=30, connect="qemu:///system"))
        unexport.assert_not_called()


class LibvirtSegmentTests(NetlabBase):
    def test_domain_xml_maps_segment_nics_to_the_lab_network_with_their_mac(self):
        xml = ET.fromstring(vmctl.libvirt.render_domain_xml("router", self.lab["router"]))
        nics = xml.findall("devices/interface")
        self.assertEqual([n.find("source").get("network") for n in nics], ["default", "lab-lan"])
        self.assertEqual(nics[1].find("mac").get("address"), vmctl.qemu.network_specs(self.lab["router"])[1]["mac"])
        # The member is EFI with fake firmware paths: never let the host's OVMF resolve them (bare CI has none).
        with mock.patch.object(vmctl.qemu, "resolve_efi_firmware", return_value=(self.root / "CODE.fd", self.root / "VARS.fd", self.root / "vars.fd")):
            member = ET.fromstring(vmctl.libvirt.render_domain_xml("dns", self.lab["dns"]))
        self.assertEqual([n.find("source").get("network") for n in member.findall("devices/interface")], ["lab-lan"])
        legacy = ET.fromstring(vmctl.libvirt.render_domain_xml("plain", self.vm_config))
        self.assertIsNone(legacy.find("devices/interface/mac"))

    def test_export_defines_and_starts_the_missing_lab_network(self):
        (self.root / "artifacts/router").mkdir(parents=True)
        (self.root / "artifacts/router/disk.qcow2").write_bytes(b"disk")
        outputs = {("net-list", "--all", "--name"): "default\n", ("net-list", "--name"): "default\n", ("list", "--all", "--name"): ""}

        def fake_run(cmd, **kwargs):
            key = tuple(cmd[3:])
            log = kwargs.get("stdout_log")
            if log is not None:
                log.write_text(outputs.get(key, ""), encoding="utf-8")

        args = argparse.Namespace(vm="router", name=None, connect="qemu:///system", no_define=False, replace=False,
                                  autostart=False, dry_run=False)
        with mock.patch.object(vmctl.lifecycle, "running_qemu_pid", return_value=None), \
             mock.patch.object(vmctl.qemu, "qmp_command", return_value=False), \
             mock.patch("shutil.which", return_value="/usr/bin/virsh"), \
             mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run:
            self.assertEqual(self.vmctl.cmd_export_libvirt(args), 0)
        commands = [c.args[0][3:] for c in run.call_args_list if c.args[0][0] == "virsh" and c.args[0][3].startswith("net-") and c.args[0][3] != "net-list"]
        self.assertEqual([c[0] for c in commands], ["net-define", "net-start", "net-autostart"])
        network_xml = self.root / "artifacts/router/libvirt/network-lab-lan.xml"
        self.assertEqual(commands[0][1], str(network_xml))
        self.assertIn("<name>lab-lan</name>", network_xml.read_text(encoding="utf-8"))
        define = [c.args[0] for c in run.call_args_list if c.args[0][3] == "define"]
        self.assertEqual(len(define), 1)

    def test_export_skips_known_networks_and_dry_run_prints(self):
        (self.root / "artifacts/router").mkdir(parents=True)
        (self.root / "artifacts/router/disk.qcow2").write_bytes(b"disk")
        outputs = {("net-list", "--all", "--name"): "default\nlab-lan\n", ("net-list", "--name"): "default\nlab-lan\n", ("list", "--all", "--name"): ""}

        def fake_run(cmd, **kwargs):
            log = kwargs.get("stdout_log")
            if log is not None:
                log.write_text(outputs.get(tuple(cmd[3:]), ""), encoding="utf-8")

        args = argparse.Namespace(vm="router", name=None, connect="qemu:///system", no_define=False, replace=False, autostart=False, dry_run=False)
        with mock.patch.object(vmctl.lifecycle, "running_qemu_pid", return_value=None), \
             mock.patch.object(vmctl.qemu, "qmp_command", return_value=False), \
             mock.patch("shutil.which", return_value="/usr/bin/virsh"), \
             mock.patch.object(vmctl.runtime, "run", side_effect=fake_run) as run:
            self.vmctl.cmd_export_libvirt(args)
        net_cmds = [c.args[0][3] for c in run.call_args_list if c.args[0][0] == "virsh" and c.args[0][3].startswith("net-") and c.args[0][3] != "net-list"]
        self.assertEqual(net_cmds, ["net-autostart"])
        args.dry_run = True
        out = io.StringIO()
        with mock.patch.object(vmctl.lifecycle, "running_qemu_pid", return_value=None), \
             mock.patch.object(vmctl.qemu, "qmp_command", return_value=False), \
             mock.patch.object(vmctl.runtime, "run") as run, contextlib.redirect_stdout(out):
            self.vmctl.cmd_export_libvirt(args)
        self.assertIn("<name>lab-lan</name>", out.getvalue())
        self.assertTrue(all(c.kwargs.get("dry_run") for c in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
