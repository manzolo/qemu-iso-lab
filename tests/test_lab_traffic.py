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

    def sample(self, running=True, agent=False, reply=None, segment_error=None, nat=None):
        vm = {"guest_agent": agent}
        nics = [{"id": "wan", "type": "user", "mac": A},
                {"id": "lan", "type": "segment", "mac": B, "name": "test", "mcast": "239.1.2.3:34567"}]
        lab = {"members": [{"name": "testvm", "running": running}]}
        with mock.patch.object(lab_traffic.config, "get_vm", return_value=vm), \
             mock.patch.object(lab_traffic, "NAT_CAPTURE", True), \
             mock.patch.object(lab_traffic.qemu, "network_specs", return_value=nics), \
             mock.patch.object(lab_traffic.guest_agent, "command", return_value=reply) as command, \
             mock.patch.object(self.monitor, "nat", side_effect=None if nat else VMError("no QMP socket"), return_value=nat), \
             mock.patch.object(self.monitor, "segment", side_effect=segment_error,
                               return_value={"time": 2, "epoch": 1, "counters": {B: {"rx": 98, "tx": 196}}}) as segment:
            entries = self.monitor.sample({}, lab)
        return entries, command, segment

    def test_nat_capture_brings_counters_and_packets_before_the_agent(self):
        packet = {"id": 1, "protocol": "TCP", "direction": "TX"}
        entries, _, _ = self.sample(agent=True, reply=[{"hardware-address": A, "statistics": {"rx-bytes": 12, "tx-bytes": 34}}],
                                    nat={"time": 3, "epoch": 2, "packets": [packet], "counters": {"rx": 500, "tx": 700}})
        self.assertEqual((entries[0]["source"], entries[0]["rx"], entries[0]["tx"]), ("QEMU capture", 500, 700))
        self.assertTrue(entries[0]["packets_available"])
        self.assertEqual(entries[0]["packets"], [packet])

    def test_nat_capture_can_be_switched_off(self):
        with mock.patch.object(lab_traffic, "NAT_CAPTURE", False), mock.patch.object(self.monitor, "nat") as nat, \
             mock.patch.object(lab_traffic.config, "get_vm", return_value={}), \
             mock.patch.object(lab_traffic.qemu, "network_specs", return_value=[{"id": "wan", "type": "user", "mac": A}]):
            entry = self.monitor.sample({}, {"members": [{"name": "vm", "running": True}]})[0]
        nat.assert_not_called()
        self.assertFalse(entry["available"])
        self.assertIn("guest-agent", entry["reason"])

    def test_nat_without_qmp_falls_back_to_the_agent_and_says_why(self):
        entries, _, _ = self.sample()
        self.assertFalse(entries[0]["available"])
        self.assertIn("no QMP socket", entries[0]["packet_reason"])
        entries, _, _ = self.sample(agent=True, reply=[{"hardware-address": A, "statistics": {"rx-bytes": 12, "tx-bytes": 34}}])
        self.assertEqual((entries[0]["source"], entries[0]["rx"]), ("guest agent", 12))
        self.assertFalse(entries[0]["packets_available"])

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

    def test_segment_packets_are_available_even_when_agent_supplies_counters(self):
        entries, _, segment = self.sample(agent=True, reply=[
            {"hardware-address": B, "statistics": {"rx-bytes": 12, "tx-bytes": 34}},
        ])
        segment.assert_called_once()
        self.assertEqual((entries[1]["source"], entries[1]["rx"]), ("guest agent", 12))
        self.assertTrue(entries[1]["packets_available"])
        self.assertFalse(entries[0]["packets_available"])

    def test_capture_failure_preserves_agent_counters_but_reports_sniffer_failure(self):
        entries, _, _ = self.sample(agent=True, reply=[
            {"hardware-address": B, "statistics": {"rx-bytes": 12, "tx-bytes": 34}},
        ], segment_error=OSError("capture failed"))
        self.assertTrue(entries[1]["available"])
        self.assertFalse(entries[1]["packets_available"])
        self.assertIn("capture failed", entries[1]["packet_reason"])


def pcap(records, order="<"):
    """A pcap stream the way QEMU's filter-dump writes it: 24-byte header, then records."""
    import struct
    magic = b"\xd4\xc3\xb2\xa1" if order == "<" else b"\xa1\xb2\xc3\xd4"
    data = magic + struct.pack(order + "HHiIII", 2, 4, 0, 0, 128, 1)
    for length, captured in records:
        data += struct.pack(order + "IIII", 1, 0, len(captured), length) + captured
    return data


class InspectedNicTests(unittest.TestCase):
    def test_the_inspected_nic_gets_every_header_newer_than_the_page_holds(self):
        history = lab_traffic.PacketHistory()
        for i in range(60):  # one burst: more than PACKETS_PER_NIC between two polls
            history.add(frame(A, B), float(i) / 100)
        self.assertEqual(len(history.for_mac(A, 1.0)), lab_traffic.PACKETS_PER_NIC)
        every = history.for_mac(A, 1.0, since=0)
        self.assertEqual(len(every), 60)
        self.assertEqual([p["id"] for p in history.for_mac(A, 1.0, since=55)], [60, 59, 58, 57, 56])
        self.assertEqual(history.for_mac(A, 1.0, since=60), [])

    def test_the_monitor_passes_since_to_the_inspected_nic_only(self):
        monitor = lab_traffic.TrafficMonitor()
        self.addCleanup(monitor.close)
        nics = [{"id": "lan", "type": "segment", "mac": B, "name": "t", "mcast": "239.1.2.3:34567"}]
        with mock.patch.object(lab_traffic.config, "get_vm", return_value={}), \
             mock.patch.object(lab_traffic.qemu, "network_specs", return_value=nics), \
             mock.patch.object(monitor, "segment", return_value={"time": 2, "epoch": 1, "counters": {B: {"rx": 0, "tx": 0}}}) as segment:
            monitor.sample({}, {"members": [{"name": "vm", "running": True}]}, ("vm", "lan", 41))
            monitor.sample({}, {"members": [{"name": "vm", "running": True}]}, ("other", "lan", 41))
        self.assertEqual(segment.call_args_list[0].args, ("239.1.2.3:34567", [B], (B, 41)))
        self.assertEqual(segment.call_args_list[1].args, ("239.1.2.3:34567", [B]))


class PcapReaderTests(unittest.TestCase):
    def test_records_come_out_whole_whatever_the_chunking(self):
        reader = lab_traffic.PcapReader()
        stream = pcap([(1514, frame()), (60, frame(B, A)[:60])])
        out = []
        for i in range(0, len(stream), 7):  # 7-byte chunks: headers and records split anywhere
            out += list(reader.feed(stream[i:i + 7]))
        self.assertEqual([(length, len(data)) for length, data in out], [(1514, 98), (60, 60)])
        self.assertEqual(out[0][1][6:12].hex(":"), A)

    def test_big_endian_and_garbage(self):
        reader = lab_traffic.PcapReader()
        self.assertEqual([length for length, _ in reader.feed(pcap([(98, frame())], ">"))], [98])
        with self.assertRaises(ValueError):
            list(lab_traffic.PcapReader().feed(b"not a pcap at all, really not"))


class NatCaptureTests(unittest.TestCase):
    """The filter-dump is added over QMP, read as QEMU appends to it, and removed with its file."""
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.sock = self.root / "qmp.sock"
        self.sock.write_bytes(b"")
        self.calls = []
        self.objects = []

    def qmp(self, sock, command, *, arguments=None, timeout=5.0):
        self.calls.append((command, arguments))
        if command == "qom-list":
            return [{"name": "type", "type": "string"}] + [{"name": n, "type": "child<filter-dump>"} for n in self.objects]
        if command == "object-del":
            self.objects.remove(arguments["id"])
        if command == "object-add":
            if arguments["id"] in self.objects:  # what QEMU 10.2 answers (and then its monitor goes mute)
                raise VMError(f"QMP object-add: attempt to add duplicate property '{arguments['id']}' to object")
            self.objects.append(arguments["id"])
            with open(arguments["file"], "wb") as handle:  # what QEMU does: the header right away
                handle.write(pcap([]))
        return {}

    def capture(self):
        cap = lab_traffic.NatCapture("vm", {}, "n1", A, qmp=self.qmp, sock=self.sock)
        self.addCleanup(cap.close)
        return cap

    def test_counts_both_directions_and_keeps_headers_only(self):
        cap = self.capture()
        add = [args for command, args in self.calls if command == "object-add"][0]
        self.assertEqual((add["qom-type"], add["netdev"], add["maxlen"]), ("filter-dump", "n1", 128))
        self.assertTrue(add["id"].startswith("vmctl-dump-n1-"))
        self.assertEqual(add["file"], str(cap.path))
        self.assertTrue(cap.path.name.startswith("capture-n1-"))
        with open(cap.path, "ab") as handle:
            handle.write(pcap([(1514, frame(A, B)[:128]), (60, frame(B, A)[:60])])[24:])
        for _ in range(40):
            if cap.sample()["counters"]["rx"]:
                break
            import time
            time.sleep(.05)
        sample = cap.sample()
        self.assertEqual(sample["counters"], {"tx": 1514, "rx": 60})
        self.assertEqual([(p["direction"], p["bytes"]) for p in sample["packets"]], [("RX", 60), ("TX", 1514)])
        cap.close()
        self.assertEqual(self.calls[-1], ("object-del", {"id": cap.object_id}))
        self.assertFalse(cap.path.exists())
        self.assertEqual(self.objects, [])

    def test_the_capture_file_is_capped_by_truncation_not_by_qmp(self):
        cap = self.capture()
        record = pcap([(70000, frame(A, B) + bytes(60000))])[24:]  # 60 KB a record: the blocks given back are measurable
        import os
        import time
        fd = os.open(cap.path, os.O_WRONLY)  # like QEMU: one descriptor, its own offset, no O_APPEND
        self.addCleanup(os.close, fd)
        os.lseek(fd, 0, os.SEEK_END)
        with mock.patch.object(lab_traffic, "PCAP_CAP_BYTES", len(record) * 4):
            for _ in range(10):  # QEMU keeps writing at its offset past our truncation (a hole before it)
                os.write(fd, record)
                time.sleep(.3)
            for _ in range(40):
                if cap.sample()["counters"]["tx"] >= 70000 * 10:
                    break
                time.sleep(.05)
        sample = cap.sample()
        self.assertEqual(sample["counters"]["tx"], 70000 * 10, "every record counted across the truncations")
        self.assertGreater(cap.truncated, 0, "the file was truncated at least once")
        self.assertLess(cap.path.stat().st_blocks * 512, len(record) * 10, "the blocks before the cut went back to the file system")
        self.assertEqual([call[0] for call in self.calls], ["qom-list", "object-add"], "no QMP command for the cap")

    def test_a_leftover_is_deleted_and_never_added_over(self):
        self.objects.append("vmctl-dump-n1-999-7")           # a server that died with its filter on
        (self.root / "capture-n1-999-7.pcap").write_bytes(b"old")
        self.objects.append("vmctl-dump-n2-999-1")           # another NIC's: not ours to touch
        cap = self.capture()
        self.assertEqual([c[0] for c in self.calls], ["qom-list", "object-del", "object-add"])
        self.assertEqual(self.calls[1][1], {"id": "vmctl-dump-n1-999-7"})
        self.assertFalse((self.root / "capture-n1-999-7.pcap").exists(), "its file goes once its object is gone")
        self.assertEqual(sorted(self.objects), sorted(["vmctl-dump-n2-999-1", cap.object_id]))
        second = self.capture()  # two captures in one process never share an id
        self.assertNotEqual(second.object_id, cap.object_id)

    def test_a_failed_attach_is_not_retried_by_every_poll(self):
        monitor = lab_traffic.TrafficMonitor()
        self.addCleanup(monitor.close)
        with mock.patch.object(lab_traffic, "NatCapture", side_effect=VMError("QMP qom-list: timed out")) as factory:
            for _ in range(5):
                with self.assertRaises(VMError):
                    monitor.nat("vm", {}, {"id": "n1", "mac": A})
        self.assertEqual(factory.call_count, 1)

    def test_no_socket_means_no_capture(self):
        self.sock.unlink()
        with self.assertRaises(VMError):
            lab_traffic.NatCapture("vm", {}, "n1", A, qmp=self.qmp, sock=self.sock)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
