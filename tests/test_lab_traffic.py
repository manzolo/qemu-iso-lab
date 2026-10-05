"""Map activity is attributed to the actual NIC, with no invented traffic."""
import unittest
from unittest import mock

from vmctl import lab_traffic
from vmctl.errors import VMError


A, B, C = "52:54:00:00:00:01", "52:54:00:00:00:02", "52:54:00:00:00:03"


def frame(source=A, destination=B):
    return bytes.fromhex(destination.replace(":", "") + source.replace(":", "") + "0800") + bytes(84)


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.counters = {mac: {"rx": 0, "tx": 0} for mac in (A, B, C)}
        self.monitor = lab_traffic.TrafficMonitor()
        self.addCleanup(self.monitor.close)

    def test_unicast_only_lights_source_and_destination(self):
        packet = frame()
        lab_traffic.count_frame(packet, self.counters)
        self.assertEqual(self.counters, {A: {"rx": 0, "tx": len(packet)},
                                         B: {"rx": len(packet), "tx": 0}, C: {"rx": 0, "tx": 0}})
        lab_traffic.count_frame(frame(B, A), self.counters)
        self.assertEqual(self.counters[A], self.counters[B])
        self.assertEqual(self.counters[C], {"rx": 0, "tx": 0})

    def test_broadcast_and_multicast_reach_other_ports_without_echo(self):
        for destination in ("ff:ff:ff:ff:ff:ff", "33:33:00:00:00:01"):
            lab_traffic.count_frame(frame(destination=destination), self.counters)
        self.assertEqual(self.counters[A], {"rx": 0, "tx": 196})
        self.assertEqual(self.counters[B], {"rx": 196, "tx": 0})
        self.assertEqual(self.counters[C], {"rx": 196, "tx": 0})

    def test_truncated_or_unrelated_frames_do_not_create_entries(self):
        lab_traffic.count_frame(b"short", self.counters)
        lab_traffic.count_frame(frame("00:00:00:00:00:04", "00:00:00:00:00:05"), self.counters)
        self.assertEqual(self.counters, {mac: {"rx": 0, "tx": 0} for mac in (A, B, C)})

    def sample(self, running=True, agent=False, reply=None, segment_error=None):
        vm = {"guest_agent": agent}
        nics = [{"id": "wan", "type": "user", "mac": A},
                {"id": "lan", "type": "segment", "mac": B, "name": "test", "mcast": "239.1.2.3:34567"}]
        lab = {"members": [{"name": "testvm", "running": running}]}
        with mock.patch.object(lab_traffic.config, "get_vm", return_value=vm), \
             mock.patch.object(lab_traffic.qemu, "network_specs", return_value=nics), \
             mock.patch.object(lab_traffic.guest_agent, "command", return_value=reply) as command, \
             mock.patch.object(self.monitor, "segment", side_effect=segment_error,
                               return_value={"time": 2, "epoch": 1, "counters": {B: {"rx": 98, "tx": 196}}}) as segment:
            entries = self.monitor.sample({}, lab)
        return entries, command, segment

    def test_stopped_vms_are_never_probed(self):
        entries, command, segment = self.sample(running=False, agent=True)
        self.assertTrue(all(not entry["available"] for entry in entries))
        command.assert_not_called()
        segment.assert_not_called()

    def test_segment_uses_configured_endpoint_and_nat_is_explicitly_unavailable(self):
        entries, command, segment = self.sample()
        command.assert_not_called()
        segment.assert_called_once_with("239.1.2.3:34567", [B])
        self.assertFalse(entries[0]["available"])
        self.assertIn("guest-agent", entries[0]["reason"])
        self.assertEqual((entries[1]["rx"], entries[1]["tx"]), (98, 196))

    def test_agent_maps_counters_by_mac_not_interface_order(self):
        entries, command, segment = self.sample(agent=True, reply=[
            {"hardware-address": C, "statistics": {"rx-bytes": 999, "tx-bytes": 999}},
            {"hardware-address": A.upper(), "statistics": {"rx-bytes": 12, "tx-bytes": 34}},
        ])
        self.assertEqual((entries[0]["source"], entries[0]["rx"], entries[0]["tx"]), ("guest agent", 12, 34))
        self.assertEqual(entries[1]["source"], "segment frames")
        self.assertEqual(command.call_args.kwargs["timeout"], .5)

    def test_failed_capture_and_missing_agent_statistics_are_not_idle_traffic(self):
        entries, _, _ = self.sample(agent=True, reply=[{"hardware-address": A}], segment_error=OSError("denied"))
        self.assertTrue(all(not entry["available"] for entry in entries))
        self.assertIn("denied", entries[1]["reason"])

    def test_agent_failure_falls_back_to_segment(self):
        with mock.patch.object(lab_traffic.guest_agent, "enabled", return_value=True), \
             mock.patch.object(lab_traffic.guest_agent, "command", side_effect=VMError("offline")), \
             mock.patch.object(lab_traffic.config, "get_vm", return_value={}), \
             mock.patch.object(lab_traffic.qemu, "network_specs", return_value=[
                 {"id": "lan", "type": "segment", "mac": A, "name": "test", "mcast": None}]), \
             mock.patch.object(self.monitor, "segment", return_value={"time": 2, "epoch": 1, "counters": {A: {"rx": 0, "tx": 0}}}):
            entry = self.monitor.sample({}, {"members": [{"name": "vm", "running": True}]})[0]
        self.assertTrue(entry["available"])
        self.assertEqual(entry["source"], "segment frames")

    def test_failed_membership_closes_the_socket(self):
        with mock.patch.object(lab_traffic.socket, "socket") as factory:
            factory.return_value.bind.side_effect = OSError("denied")
            with self.assertRaises(OSError):
                lab_traffic.SegmentListener("239.1.2.3:34567")
            factory.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
