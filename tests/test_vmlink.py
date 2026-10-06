"""vmctl link: hot-plugged NICs on a shared segment between running VMs, recorded per segment."""

import argparse
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.lifecycle  # noqa: E402
import vmctl.qemu  # noqa: E402
import vmctl.runtime  # noqa: E402
import vmctl.vmlink as vmlink  # noqa: E402
from vmctl.errors import VMError  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class HotplugSlotTests(BaseVmctlTestCase):
    def test_q35_headless_boots_carry_empty_root_ports_after_every_other_device(self):
        vm = dict(self.vm_config, machine="q35")
        self.assertEqual(vmlink.qemu.hotplug_port_args(dict(vm, machine="pc")), [])
        with mock.patch.object(vmctl.runtime, "require_command"), mock.patch.object(vmctl.qemu, "firmware_args", return_value=[]):
            headless = vmctl.qemu.common_args(vm, None, dry_run=True, headless=True, allow_missing_disk=True, no_reboot=True)
            windowed = vmctl.qemu.common_args(vm, None, dry_run=True, headless=False, allow_missing_disk=True)
        self.assertEqual(headless[-5:], ["-device", "pcie-root-port,id=hotplug0,chassis=200",
                                         "-device", "pcie-root-port,id=hotplug1,chassis=201", "-no-reboot"])
        self.assertLess(headless.index("-netdev"), headless.index("pcie-root-port,id=hotplug0,chassis=200"))
        self.assertNotIn("pcie-root-port,id=hotplug0,chassis=200", windowed)


class RecordTests(BaseVmctlTestCase):
    def test_addresses_and_macs_are_stable_and_never_collide_on_a_segment(self):
        record = {"segment": "session", "members": {"a": {"address": "192.168.100.1/24", "pid": 1}}}
        self.assertEqual(vmlink.next_address(record), "192.168.100.2/24")
        self.assertEqual(vmlink.subnet("session"), "192.168.100")
        self.assertRegex(vmlink.subnet("backend"), r"^192\.168\.\d+$")
        self.assertNotEqual(vmlink.subnet("backend"), "192.168.100")
        self.assertEqual(vmlink.parse_subnet("172.16.5.0/24"), "172.16.5")
        with self.assertRaisesRegex(VMError, "expected something like"):
            vmlink.parse_subnet("172.16.5.0/16")
        custom = {"segment": "backend", "subnet": "172.16.5", "members": {}}
        self.assertEqual(vmlink.next_address(custom), "172.16.5.1/24")
        self.assertEqual(vmlink.nic_mac("session", "kali"), vmlink.nic_mac("session", "kali"))
        self.assertNotEqual(vmlink.nic_mac("session", "kali"), vmlink.nic_mac("session", "freebsd"))
        self.assertTrue(vmlink.nic_mac("session", "kali").startswith("52:54:01:"))

    def test_a_stopped_member_goes_back_to_pending_and_keeps_its_place(self):
        vmlink.save_record({"segment": "session", "members": {
            "a": {"address": "192.168.100.1/24", "mac": "52:54:01:00:00:0a", "pid": 1, "interface": "eth1", "bus": "hotplug0"},
            "b": {"address": "192.168.100.2/24", "pid": 2}}})
        with mock.patch.object(vmlink, "qemu_alive", side_effect=lambda pid: pid == 2):
            self.assertEqual(vmlink.links_of("b"), [{"segment": "session", "address": "192.168.100.2/24", "up": True,
                                                     "peers": [{"name": "a", "address": "192.168.100.1/24", "up": False}]}])
            labs = vmlink.session_labs()
            self.assertEqual(vmlink.pending_segments("a"), ["session"])
        self.assertEqual(vmlink.load_record("session")["members"]["a"], {"address": "192.168.100.1/24", "mac": "52:54:01:00:00:0a"})
        self.assertEqual(labs[0]["members"], ["a", "b"])
        self.assertEqual(labs[0]["pending"], ["a"])
        self.assertTrue(labs[0]["session"] and labs[0]["lab"])
        with mock.patch.object(vmlink, "qemu_alive", return_value=False):
            self.assertEqual(len(vmlink.all_records()), 1)  # only --off forgets a segment

    def test_a_stopped_vm_is_recorded_pending_and_boots_with_the_nic(self):
        vm = dict(self.vm_config, machine="q35", network_device="e1000")
        with mock.patch.object(vmctl.qemu, "qmp_execute") as qmp:
            member = vmlink.link(vm, "off", None, mcast="239.1.2.3:4000")
        qmp.assert_not_called()
        self.assertTrue(member["pending"])
        self.assertEqual(member["address"], "192.168.100.1/24")
        self.assertEqual(vmlink.load_record("session")["mcast"], "239.1.2.3:4000")
        self.assertTrue(vmlink.link(vm, "off", None)["already"])
        self.assertEqual(vmlink.boot_args("off", vm), [
            "-netdev", "socket,id=link-session,mcast=239.1.2.3:4000,localaddr=127.0.0.1",
            "-device", f"e1000,netdev=link-session,id=link-session-nic,mac={vmlink.nic_mac('session', 'off')}"])
        self.assertEqual(vmlink.boot_args("stranger", vm), [])
        with mock.patch.object(vmlink, "qemu_alive", return_value=True), \
                mock.patch.object(vmlink, "configure_guest", return_value="enp0s5") as configure:
            results = vmlink.settle("off", vm, 77)
            self.assertEqual(vmlink.boot_args("off", vm), [])  # up now: a second start adds nothing
        configure.assert_called_once_with(vm, vmlink.nic_mac("session", "off"), "192.168.100.1/24")
        self.assertEqual((results[0]["segment"], results[0]["interface"], results[0]["pid"]), ("session", "enp0s5", 77))


class LinkTests(BaseVmctlTestCase):
    def setUp(self):
        super().setUp()
        self.vm = dict(self.vm_config, machine="q35", meta={"family": "debian"},
                       ssh_provision={"user": "lab", "ssh_host_port": 2299})
        sock = vmctl.qemu.qmp_socket_path(self.vm)
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.write_text("")
        self.alive = mock.patch.object(vmlink, "qemu_alive", return_value=True)
        self.alive.start()
        self.addCleanup(self.alive.stop)

    def test_link_plugs_records_and_configures_then_is_idempotent(self):
        calls = []
        with mock.patch.object(vmctl.qemu, "qmp_execute", side_effect=lambda s, c, arguments=None, **kw: calls.append((c, arguments))), \
                mock.patch.object(vmlink, "configure_guest", return_value="enp1s0") as configure:
            member = vmlink.link(self.vm, "kali", 4242)
            again = vmlink.link(self.vm, "kali", 4242)
        self.assertEqual([c for c, _ in calls], ["netdev_add", "device_add"])
        self.assertEqual(calls[0][1]["type"], "socket")
        self.assertEqual(calls[0][1]["mcast"], vmctl.qemu.segment_endpoint("session"))
        self.assertEqual(calls[1][1]["bus"], "hotplug0")
        self.assertEqual(calls[1][1]["mac"], vmlink.nic_mac("session", "kali"))
        self.assertEqual((member["address"], member["interface"], member["configured"], member["already"]), ("192.168.100.1/24", "enp1s0", True, False))
        self.assertTrue(again["already"])
        configure.assert_called_once()
        self.assertEqual(vmlink.load_record("session")["members"]["kali"]["pid"], 4242)

    def test_a_pc_machine_needs_no_bus_and_a_second_segment_takes_the_next_slot(self):
        calls = []
        with mock.patch.object(vmctl.qemu, "qmp_execute", side_effect=lambda s, c, arguments=None, **kw: calls.append((c, arguments))), \
                mock.patch.object(vmlink, "configure_guest", return_value=None):
            vmlink.link(dict(self.vm, machine="pc"), "old", 1)
            vmlink.link(self.vm, "kali", 2)
            vmlink.link(self.vm, "kali", 2, segment="backend")
        device_adds = [a for c, a in calls if c == "device_add"]
        self.assertNotIn("bus", device_adds[0])
        self.assertEqual([a["bus"] for a in device_adds[1:]], ["hotplug0", "hotplug1"])
        self.assertEqual(vmlink.links_of("kali"), [
            {"segment": "backend", "address": vmlink.subnet("backend") + ".1/24", "up": True, "peers": []},
            {"segment": "session", "address": "192.168.100.2/24", "up": True,
             "peers": [{"name": "old", "address": "192.168.100.1/24", "up": True}]}])

    def test_a_refused_device_add_removes_the_netdev_and_explains_an_old_boot(self):
        calls = []

        def qmp(sock, command, arguments=None, **kw):
            calls.append(command)
            if command == "device_add":
                raise VMError("QMP device_add: Bus 'hotplug0' not found")

        with mock.patch.object(vmctl.qemu, "qmp_execute", side_effect=qmp), \
                mock.patch.object(vmctl.qemu, "qmp_command", side_effect=lambda s, c, **kw: calls.append(c) or True):
            with self.assertRaisesRegex(VMError, "boot it headless again"):
                vmlink.link(self.vm, "kali", 7)
        self.assertEqual(calls, ["netdev_add", "device_add", "netdev_del"])
        self.assertFalse(vmlink.record_path("session").exists())

    def test_a_failed_guest_step_keeps_the_nic_recorded_with_the_error(self):
        with mock.patch.object(vmctl.qemu, "qmp_execute"), \
                mock.patch.object(vmlink, "configure_guest", side_effect=VMError("could not configure the guest over SSH (exit 3): no interface")):
            member = vmlink.link(self.vm, "kali", 9)
        self.assertFalse(member["configured"])
        self.assertIn("no interface", member["error"])
        self.assertIn("kali", vmlink.load_record("session")["members"])

    def test_no_qmp_socket_means_a_windowed_vm(self):
        vmctl.qemu.qmp_socket_path(self.vm).unlink()
        with self.assertRaisesRegex(VMError, "booted headless"):
            vmlink.hotplug(self.vm, "kali", 1, "session", "52:54:01:00:00:01")

    def test_unlink_unplugs_and_forgets(self):
        with mock.patch.object(vmctl.qemu, "qmp_execute"), mock.patch.object(vmlink, "configure_guest", return_value=None):
            vmlink.link(self.vm, "kali", 3)
        with mock.patch.object(vmctl.qemu, "qmp_command", return_value=True) as qmp:
            self.assertEqual(vmlink.unlink(self.vm, "kali", "session"), "removed")
            self.assertEqual(vmlink.unlink(self.vm, "kali", "session"), "absent")
        self.assertEqual([c.args[1] for c in qmp.call_args_list], ["device_del", "netdev_del"])
        self.assertFalse(vmlink.record_path("session").exists())

    def test_unlink_of_a_pending_member_touches_no_qemu_and_a_refused_unplug_says_so(self):
        vmlink.link(self.vm, "off", None)
        with mock.patch.object(vmctl.qemu, "qmp_command") as qmp:
            self.assertEqual(vmlink.unlink(self.vm, "off", "session"), "removed")
        qmp.assert_not_called()
        with mock.patch.object(vmctl.qemu, "qmp_execute"), mock.patch.object(vmlink, "configure_guest", return_value=None):
            vmlink.link(self.vm, "kali", 3)
        with mock.patch.object(vmctl.qemu, "qmp_command", return_value=False):  # q35 root bus: not hot-pluggable
            self.assertEqual(vmlink.unlink(self.vm, "kali", "session"), "stays")


class GuestScriptTests(unittest.TestCase):
    def test_linux_and_freebsd_find_the_interface_by_mac_and_windows_has_no_shell_script(self):
        linux = vmlink.guest_script("debian", "52:54:01:aa:bb:cc", "192.168.100.1/24")
        self.assertIn("ip -o link", linux)
        self.assertIn("ip addr replace 192.168.100.1/24", linux)
        self.assertIn("nmcli con add type ethernet", linux)  # NetworkManager guests keep the address this way
        self.assertIn("ipv4.addresses 192.168.100.1/24", linux)
        self.assertIn("52:54:01:aa:bb:cc", linux)
        # systemd-networkd leaves the NIC alone, or its wait-online holds the boot (Ubuntu 26.04, 2026-10-06)
        self.assertIn("Unmanaged=yes", linux)
        self.assertIn("MACAddress=52:54:01:aa:bb:cc", linux)
        self.assertIn("networkctl reload", linux)
        self.assertLess(linux.index("networkctl reload"), linux.index('echo "$IF"'))
        bsd = vmlink.guest_script("freebsd", "52:54:01:aa:bb:cc", "192.168.100.1/24")
        self.assertIn("ifconfig \"$IF\" inet 192.168.100.1/24 up", bsd)
        self.assertIsNone(vmlink.guest_script("windows", "52:54:01:aa:bb:cc", "192.168.100.1/24"))
        self.assertIn("netsh", vmlink.manual_hint({"meta": {"family": "windows"}}, "192.168.100.1/24"))

    def test_configure_guest_skips_guests_without_ssh_or_a_known_family(self):
        self.assertIsNone(vmlink.configure_guest({"meta": {"family": "debian"}}, "52:54:01:00:00:01", "192.168.100.1/24"))
        self.assertIsNone(vmlink.configure_guest({"meta": {"family": "windows"}, "ssh_provision": {"user": "lab", "ssh_host_port": 2}}, "52:54:01:00:00:01", "192.168.100.1/24"))
        seven = {"meta": {"family": "windows"}, "windows_config": {"edition": "Windows 7 Ultimate"}, "ssh_provision": {"user": "lab", "ssh_host_port": 2}}
        self.assertIsNone(vmlink.configure_guest(seven, "52:54:01:00:00:01", "192.168.100.1/24"))

    def test_windows_10_and_11_are_configured_with_powershell_over_ssh(self):
        import base64
        vm = {"meta": {"family": "windows"}, "windows_config": {"edition": "Windows 11 Pro"},
              "ssh_provision": {"user": "lab", "ssh_host_port": 2235}}
        self.assertTrue(vmlink.configures_windows(vm))
        self.assertFalse(vmlink.configures_windows({"windows_config": {"edition": "Windows 7 Professional"}}))
        script = vmlink.windows_script("52:54:01:e0:1f:a4", "192.168.100.3/24")
        self.assertIn("MacAddress -eq '52-54-01-E0-1F-A4'", script)
        self.assertIn("-IPAddress 192.168.100.3 -PrefixLength 24", script)
        self.assertIn("-Dhcp Disabled", script)
        self.assertIn("-NetworkCategory Private", script)
        self.assertIn("-Protocol ICMPv4 -IcmpType 8", script)
        done = subprocess.CompletedProcess([], 0, stdout="Ethernet 3\r\n", stderr="")
        with mock.patch.object(vmlink.ssh, "ssh_base_cmd", return_value=["ssh", "lab@127.0.0.1"]), \
                mock.patch.object(vmlink.subprocess, "run", return_value=done) as run:
            self.assertEqual(vmlink.configure_guest(vm, "52:54:01:e0:1f:a4", "192.168.100.3/24"), "Ethernet 3")
        remote = run.call_args.args[0][-1]
        self.assertTrue(remote.startswith("powershell -NoProfile -NonInteractive -EncodedCommand "))
        self.assertNotIn("sudo", remote)
        decoded = base64.b64decode(remote.rsplit(" ", 1)[1]).decode("utf-16-le")
        self.assertEqual(decoded, script)


class CommandTests(BaseVmctlTestCase):
    def namespace(self, **kw):
        base = dict(vm=None, peers=[], segment=None, mcast=None, subnet=None, status=False, off=False, settle=None, dry_run=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_stopped_vms_are_linked_pending_and_a_lone_vm_is_refused(self):
        self.write_config_dir()
        with self.assertRaisesRegex(VMError, "needs two VMs"):
            vmctl.lifecycle.cmd_link(self.namespace(vm=self.vm_name))
        with mock.patch.object(vmctl.lifecycle, "running_background_pid", return_value=None), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(vmctl.lifecycle.cmd_link(self.namespace(vm=self.vm_name, peers=[self.vm_name])), 0)
        self.assertIn("joins segment session", out.getvalue())
        self.assertIn(self.vm_name, vmlink.load_record("session")["members"])

    def test_start_boots_a_pending_member_with_its_nic_and_detaches_the_settler(self):
        self.write_config_dir()
        vm = vmctl.config.get_vm(vmctl.config.load_config(), self.vm_name)
        vmlink.link(vm, self.vm_name, None)
        with mock.patch.object(vmctl.qemu, "common_args", return_value=["qemu-system-x86_64"]), \
                mock.patch.object(vmctl.runtime, "run") as run, \
                mock.patch.object(vmctl.lifecycle, "start_link_settler") as settler, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            vmctl.lifecycle.cmd_start(argparse.Namespace(vm=self.vm_name, video=None, dry_run=False, headless=False,
                                                         background=False, spice_port=None, cloud_init=False))
        command = run.call_args.args[0]
        self.assertIn("socket,id=link-session,mcast=" + vmctl.qemu.segment_endpoint("session") + ",localaddr=127.0.0.1", command)
        settler.assert_called_once_with(self.vm_name, dry_run=False)

    def test_status_and_off_on_an_empty_segment_are_quiet(self):
        self.write_config_dir()
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(vmctl.lifecycle.cmd_link(self.namespace()), 0)
            self.assertEqual(vmctl.lifecycle.cmd_link(self.namespace(off=True)), 0)
        self.assertIn("No VM is linked", out.getvalue())
        self.assertIn("Nothing is linked", out.getvalue())


if __name__ == "__main__":
    unittest.main()
