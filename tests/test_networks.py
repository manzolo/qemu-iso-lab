import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import vmctl.errors  # noqa: E402
import vmctl.qemu  # noqa: E402

from tests._common import BaseVmctlTestCase  # noqa: E402


class NetworkSpecTests(BaseVmctlTestCase):
    """The ``networks`` list of a profile: several NICs, per phase, on slirp or on a shared segment."""

    def test_profiles_without_networks_keep_the_single_legacy_slirp_nic(self):
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2299}
        specs = vmctl.qemu.network_specs(self.vm_config)
        self.assertEqual(len(specs), 1)
        self.assertTrue(specs[0]["legacy"])
        self.assertEqual(vmctl.qemu.network_args(self.vm_config),
                         ["-netdev", "user,id=n1,hostfwd=tcp:127.0.0.1:2299-:22", "-device", "virtio-net-pci,netdev=n1"])
        self.assertEqual(vmctl.qemu.network_args(self.vm_config, "install"), vmctl.qemu.network_args(self.vm_config, "runtime"))
        self.vm_config["network"] = "bridge"
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.qemu.network_args(self.vm_config)

    def test_common_args_without_networks_is_byte_identical_for_both_phases(self):
        self.create_disk()
        with mock.patch.object(shutil, "which", return_value="/usr/bin/qemu-system-x86_64"):
            runtime_args = vmctl.qemu.common_args(self.vm_config, None, headless=True)
            install_args = vmctl.qemu.common_args(self.vm_config, None, headless=True, network_phase="install")
        self.assertEqual(runtime_args, install_args)
        self.assertIn("virtio-net-pci,netdev=n1", runtime_args)
        self.assertFalse(any("mac=" in a for a in runtime_args))

    def test_phase_filter_keeps_the_slot_and_the_mac_of_a_member_nic(self):
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2238}
        self.vm_config["networks"] = [
            {"id": "install", "type": "user", "phase": "install"},
            {"id": "lan", "type": "segment", "name": "lab-lan", "phase": "runtime"},
        ]
        install = vmctl.qemu.network_specs(self.vm_config, "install")
        run = vmctl.qemu.network_specs(self.vm_config, "runtime")
        self.assertEqual([s["type"] for s in install], ["user"])
        self.assertEqual([s["type"] for s in run], ["segment"])
        self.assertEqual(install[0]["mac"], run[0]["mac"])  # same slot -> same MAC -> same guest interface
        self.assertEqual(run[0]["mac"], vmctl.qemu.default_nic_mac(self.vm_config, 0))
        self.assertTrue(run[0]["mac"].startswith("52:54:00:"))
        install_args = vmctl.qemu.network_args(self.vm_config, "install")
        self.assertEqual(install_args[1], "user,id=install,hostfwd=tcp:127.0.0.1:2238-:22")
        self.assertEqual(install_args[3], f"virtio-net-pci,netdev=install,mac={run[0]['mac']}")
        runtime_args = vmctl.qemu.network_args(self.vm_config, "runtime")
        self.assertTrue(runtime_args[1].startswith("socket,id=lan,mcast=239."))
        self.assertNotIn("hostfwd", runtime_args[1])

    def test_default_mac_is_stable_per_disk_and_slot(self):
        first = vmctl.qemu.default_nic_mac(self.vm_config, 0)
        self.assertEqual(first, vmctl.qemu.default_nic_mac(self.vm_config, 0))
        self.assertNotEqual(first, vmctl.qemu.default_nic_mac(self.vm_config, 1))
        other = dict(self.vm_config, disk=dict(self.vm_config["disk"], path="artifacts/other/disk.qcow2"))
        self.assertNotEqual(first, vmctl.qemu.default_nic_mac(other, 0))

    def test_segment_endpoint_is_deterministic_and_overridable(self):
        endpoint = vmctl.qemu.segment_endpoint("lab-lan")
        self.assertEqual(endpoint, vmctl.qemu.segment_endpoint("lab-lan"))
        self.assertNotEqual(endpoint, vmctl.qemu.segment_endpoint("other-lan"))
        group, port = endpoint.rsplit(":", 1)
        self.assertTrue(group.startswith("239."))
        self.assertTrue(20000 <= int(port) < 60000)
        self.assertEqual(vmctl.qemu.segment_endpoint("x", "239.1.2.3:4444"), "239.1.2.3:4444")
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.qemu.segment_endpoint("x", "10.0.0.1:80")

    def test_router_keeps_wan_and_lan_in_both_phases_with_extra_forwards(self):
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2237}
        self.vm_config["networks"] = [
            {"id": "wan", "type": "user", "hostfwd": [{"host_port": 8080, "guest_port": 80}]},
            {"id": "lan", "type": "segment", "name": "lab-lan", "mac": "52:54:00:aa:bb:cc"},
        ]
        for phase in ("install", "runtime"):
            args = vmctl.qemu.network_args(self.vm_config, phase)
            self.assertEqual(args[1], "user,id=wan,hostfwd=tcp:127.0.0.1:2237-:22,hostfwd=tcp:127.0.0.1:8080-:80")
            self.assertEqual(args[7], "virtio-net-pci,netdev=lan,mac=52:54:00:aa:bb:cc")

    def test_only_the_first_user_nic_gets_the_ssh_forward_unless_told_otherwise(self):
        self.vm_config["ssh_provision"] = {"user": "lab", "ssh_host_port": 2250}
        self.vm_config["networks"] = [{"type": "user", "ssh": False}, {"type": "user"}]
        args = vmctl.qemu.network_args(self.vm_config)
        self.assertEqual(args[1], "user,id=net0")
        self.assertEqual(args[5], "user,id=net1,hostfwd=tcp:127.0.0.1:2250-:22")

    def test_invalid_network_lists_are_rejected(self):
        for bad in (
            [],
            [{"type": "bridge"}],
            [{"type": "segment"}],  # no name
            [{"type": "segment", "name": "x", "hostfwd": [{"host_port": 1, "guest_port": 2}]}],
            [{"type": "user", "phase": "sometimes"}],
            [{"type": "user", "mac": "not-a-mac"}],
            [{"type": "user", "hostfwd": [{"host_port": 1}]}],
            "user",
        ):
            self.vm_config["networks"] = bad
            with self.subTest(bad=bad), self.assertRaises(vmctl.errors.VMError):
                vmctl.qemu.network_specs(self.vm_config)
        self.vm_config["networks"] = [{"type": "user", "phase": "install"}]
        with self.assertRaises(vmctl.errors.VMError):
            vmctl.qemu.network_specs(self.vm_config, "runtime")  # nothing left in this phase


if __name__ == "__main__":
    unittest.main()
