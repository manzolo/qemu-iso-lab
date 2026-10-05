import ipaddress
import json
import random
import struct
import unittest

from vmctl import lab_traffic, packet_summary


SOURCE = "52:54:00:00:00:01"
DESTINATION = "52:54:00:00:00:02"


def ethernet(payload, kind=0x0800):
    return bytes.fromhex("525400000002525400000001") + struct.pack("!H", kind) + payload


def ipv4(payload, protocol=1, fragment=0):
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 1, fragment, 64, protocol, 0,
                       ipaddress.IPv4Address("172.20.6.1").packed, ipaddress.IPv4Address("172.20.6.11").packed) + payload


def ipv6(payload, protocol=58):
    return struct.pack("!IHBB16s16s", 6 << 28, len(payload), protocol, 64,
                       ipaddress.IPv6Address("fd00::1").packed, ipaddress.IPv6Address("fd00::2").packed) + payload


def echo(kind=8, sequence=7):
    return struct.pack("!BBHHH", kind, 0, 0, 42, sequence)


class PacketSummaryTests(unittest.TestCase):
    def test_ping_request_and_reply_show_sequence_without_payload(self):
        for kind, label in ((8, "Echo request"), (0, "Echo reply")):
            raw = ethernet(ipv4(echo(kind) + b"private-payload"))
            result = packet_summary.summarize(raw)
            self.assertEqual(result["protocol"], "ICMP")
            self.assertEqual(result["source"], "172.20.6.1")
            self.assertEqual(result["destination"], "172.20.6.11")
            self.assertEqual(result["info"], label + " · id=42 seq=7")
            self.assertEqual(result["bytes"], len(raw))
            self.assertNotIn("private-payload", json.dumps(result))

    def test_tcp_ports_flags_and_udp_do_not_guess_application_protocols(self):
        tcp = struct.pack("!HHIIBBHHH", 45000, 443, 1, 0, 0x50, 0x12, 1024, 0, 0)
        result = packet_summary.summarize(ethernet(ipv4(tcp, 6)))
        self.assertEqual((result["protocol"], result["source_port"], result["destination_port"], result["info"]),
                         ("TCP", 45000, 443, "SYN ACK"))
        udp = struct.pack("!HHHH", 50000, 53, 8, 0)
        result = packet_summary.summarize(ethernet(ipv4(udp, 17)))
        self.assertEqual((result["protocol"], result["destination_port"]), ("UDP", 53))

    def test_arp_and_vlan(self):
        arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, bytes.fromhex("525400000001"),
                          ipaddress.IPv4Address("172.20.6.1").packed, bytes(6), ipaddress.IPv4Address("172.20.6.11").packed)
        result = packet_summary.summarize(ethernet(struct.pack("!HH", 42, 0x0806) + arp, 0x8100))
        self.assertEqual(result["protocol"], "ARP")
        self.assertEqual(result["info"], "VLAN 42 · Who has this IP?")
        self.assertEqual(result["destination"], "172.20.6.11")

    def test_ipv6_echo_and_extensions(self):
        extension = bytes([58, 0]) + bytes(6)
        result = packet_summary.summarize(ethernet(ipv6(extension + echo(128), 0), 0x86dd))
        self.assertEqual((result["protocol"], result["source"], result["info"]),
                         ("ICMPv6", "fd00::1", "Echo request · id=42 seq=7"))

    def test_non_initial_fragments_never_invent_ports_or_ping_details(self):
        for raw in (ethernet(ipv4(echo(), 6, fragment=1)),
                    ethernet(ipv6(bytes([6, 0, 0, 8]) + bytes(4) + echo(), 44), 0x86dd)):
            result = packet_summary.summarize(raw)
            self.assertIn("fragment", result["info"])
            self.assertNotIn("source_port", result)

    def test_short_transport_and_options_never_read_ethernet_padding_as_ports(self):
        result = packet_summary.summarize(ethernet(ipv4(b"", 6)) + bytes(60))
        self.assertNotIn("source_port", result)
        self.assertIn("truncated", result["info"])
        tcp = bytes(12) + bytes([0xf0, 0x02]) + bytes(6)
        self.assertIn("truncated", packet_summary.summarize(ethernet(ipv4(tcp, 6)))["info"])

    def test_truncated_unknown_and_random_frames_are_safe(self):
        self.assertIsNone(packet_summary.summarize(bytes(13)))
        self.assertEqual(packet_summary.summarize(ethernet(bytes(8), 0x88cc))["protocol"], "Ethernet")
        rng = random.Random(17)
        for _ in range(1000):
            body = rng.randbytes(rng.randrange(300))
            packet_summary.summarize(ethernet(body, rng.choice((0x0800, 0x86dd, 0x0806, 0x8100))))

    def test_history_filters_per_cable_direction_and_expires(self):
        history = lab_traffic.PacketHistory()
        history.add(ethernet(ipv4(echo())), 100)
        self.assertEqual(history.for_mac(SOURCE, 101)[0]["direction"], "TX")
        self.assertEqual(history.for_mac(DESTINATION, 101)[0]["direction"], "RX")
        self.assertEqual(history.for_mac("52:54:00:00:00:03", 101), [])
        self.assertEqual(history.for_mac(SOURCE, 131), [])

    def test_history_is_bounded_sampled_and_newest_first(self):
        history = lab_traffic.PacketHistory()
        for i in range(500):
            history.add(ethernet(ipv4(echo(sequence=i))), 100)
        self.assertEqual(len(history.records), lab_traffic.PACKETS_PER_SECOND)
        self.assertEqual(len(history.for_mac(SOURCE, 100)), lab_traffic.PACKETS_PER_NIC)
        self.assertIn("seq=99", history.for_mac(SOURCE, 100)[0]["info"])
        for i in range(500):
            history.add(ethernet(ipv4(echo(sequence=i))), 101)
        self.assertEqual(len(history.records), lab_traffic.PACKET_HISTORY)


if __name__ == "__main__":
    unittest.main()


class PacketDetailTests(unittest.TestCase):
    """The detail view of a row: decoded layers and the header bytes, never the payload."""

    def test_layers_and_header_hex_stop_at_the_transport_header(self):
        tcp = struct.pack("!HHIIBBHHH", 45000, 443, 7, 9, 0x60, 0x18, 1024, 0, 0) + bytes([1, 1, 1, 1]) + b"SECRET-PAYLOAD"
        raw = ethernet(ipv4(tcp, 6))
        result = packet_summary.summarize(raw)
        self.assertEqual([layer["name"] for layer in result["layers"]], ["Ethernet", "IPv4", "TCP"])
        fields = {name: value for layer in result["layers"] for name, value in layer["fields"]}
        self.assertEqual(fields["Type"], "IPv4 (0x0800)")
        self.assertEqual(fields["TTL"], "64")
        self.assertEqual(fields["Protocol"], "TCP (6)")
        self.assertEqual(fields["Sequence number"], "7")
        self.assertEqual(fields["Header length"], "24 bytes (4 bytes of options)")
        self.assertEqual(fields["Flags"], "ACK PSH (0x18)")
        self.assertEqual(fields["Payload"], "14 bytes (not captured)")
        header = bytes.fromhex(result["header_hex"])
        self.assertEqual(len(header), 14 + 20 + 24)
        self.assertNotIn(b"SECRET", header)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_every_kind_of_frame_has_layers_and_bounded_hex(self):
        arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 2, bytes.fromhex("525400000001"),
                          ipaddress.IPv4Address("172.20.6.1").packed, bytes(6), ipaddress.IPv4Address("172.20.6.11").packed)
        frames = {
            "ARP": ethernet(arp, 0x0806), "ICMP": ethernet(ipv4(echo() + b"x" * 56)),
            "UDP": ethernet(ipv4(struct.pack("!HHHH", 50000, 53, 20, 0) + b"q" * 12, 17)),
            "ICMPv6": ethernet(ipv6(echo(128) + b"y" * 8), 0x86dd), "Ethernet": ethernet(b"\x00" * 40, 0x1234),
        }
        for name, raw in frames.items():
            result = packet_summary.summarize(raw)
            self.assertEqual(result["protocol"], name)
            self.assertTrue(result["layers"], name)
            header = bytes.fromhex(result["header_hex"])
            self.assertTrue(14 <= len(header) <= len(raw), name)
            self.assertNotIn(b"x" * 8, header)
            self.assertNotIn(b"q" * 8, header)
        # A frame too short for its headers still describes what it has, with the hex of what was read.
        result = packet_summary.summarize(ethernet(b"\x00" * 10))
        self.assertIn("truncated", result["info"].lower())
        self.assertGreaterEqual(len(result["header_hex"]), 28)
