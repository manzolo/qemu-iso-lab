"""vmctl link: hot-plugged NICs on a shared segment between running VMs, recorded per segment."""

import argparse
import io
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

    def test_dead_members_are_pruned_and_an_empty_segment_is_forgotten(self):
        vmlink.save_record({"segment": "session", "members": {"a": {"address": "192.168.100.1/24", "pid": 1}, "b": {"address": "192.168.100.2/24", "pid": 2}}})
        with mock.patch.object(vmlink, "qemu_alive", side_effect=lambda pid: pid == 2):
            self.assertEqual(list(vmlink.load_record("session")["members"]), ["a", "b"])
            self.assertEqual(vmlink.links_of("b"), [{"segment": "session", "address": "192.168.100.2/24", "peers": []}])
            self.assertEqual(vmlink.links_of("a"), [])
            labs = vmlink.session_labs()
        self.assertEqual(labs[0]["members"], ["b"])
        self.assertTrue(labs[0]["session"] and labs[0]["lab"])
        self.assertEqual(labs[0]["addresses"], {"b": ["192.168.100.2/24"]})
        with mock.patch.object(vmlink, "qemu_alive", return_value=False):
            self.assertEqual(vmlink.all_records(), [])
        self.assertFalse(vmlink.record_path("session").exists())


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
            {"segment": "backend", "address": vmlink.subnet("backend") + ".1/24", "peers": []},
            {"segment": "session", "address": "192.168.100.2/24", "peers": [{"name": "old", "address": "192.168.100.1/24"}]}])

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
            self.assertTrue(vmlink.unlink(self.vm, "kali", "session"))
            self.assertFalse(vmlink.unlink(self.vm, "kali", "session"))
        self.assertEqual([c.args[1] for c in qmp.call_args_list], ["device_del", "netdev_del"])
        self.assertFalse(vmlink.record_path("session").exists())


class GuestScriptTests(unittest.TestCase):
    def test_linux_and_freebsd_find_the_interface_by_mac_and_windows_is_left_to_the_user(self):
        linux = vmlink.guest_script("debian", "52:54:01:aa:bb:cc", "192.168.100.1/24")
        self.assertIn("ip -o link", linux)
        self.assertIn("ip addr replace 192.168.100.1/24", linux)
        self.assertIn("52:54:01:aa:bb:cc", linux)
        bsd = vmlink.guest_script("freebsd", "52:54:01:aa:bb:cc", "192.168.100.1/24")
        self.assertIn("ifconfig \"$IF\" inet 192.168.100.1/24 up", bsd)
        self.assertIsNone(vmlink.guest_script("windows", "52:54:01:aa:bb:cc", "192.168.100.1/24"))
        self.assertIn("netsh", vmlink.manual_hint({"meta": {"family": "windows"}}, "192.168.100.1/24"))

    def test_configure_guest_skips_guests_without_ssh_or_a_known_family(self):
        self.assertIsNone(vmlink.configure_guest({"meta": {"family": "debian"}}, "52:54:01:00:00:01", "192.168.100.1/24"))
        self.assertIsNone(vmlink.configure_guest({"meta": {"family": "windows"}, "ssh_provision": {"user": "lab", "ssh_host_port": 2}}, "52:54:01:00:00:01", "192.168.100.1/24"))


class CommandTests(BaseVmctlTestCase):
    def namespace(self, **kw):
        base = dict(vm=None, peers=[], segment=None, mcast=None, subnet=None, status=False, off=False, dry_run=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_link_refuses_a_stopped_vm_and_a_lone_vm_on_an_empty_segment(self):
        self.write_config_dir()
        with mock.patch.object(vmctl.lifecycle, "running_background_pid", return_value=None):
            with self.assertRaisesRegex(VMError, "not running in the background"):
                vmctl.lifecycle.cmd_link(self.namespace(vm=self.vm_name, peers=[self.vm_name]))
        with self.assertRaisesRegex(VMError, "needs two running VMs"):
            vmctl.lifecycle.cmd_link(self.namespace(vm=self.vm_name))

    def test_status_and_off_on_an_empty_segment_are_quiet(self):
        self.write_config_dir()
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(vmctl.lifecycle.cmd_link(self.namespace()), 0)
            self.assertEqual(vmctl.lifecycle.cmd_link(self.namespace(off=True)), 0)
        self.assertIn("No running VM is linked", out.getvalue())
        self.assertIn("Nothing is linked", out.getvalue())


if __name__ == "__main__":
    unittest.main()
