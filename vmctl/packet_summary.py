"""Bounded Ethernet/IP header summaries for the lab map; never retain payloads.

IPv4/ICMP layouts: RFC 791/792; IPv6 extensions: RFC 8200; TCP: RFC 9293.
This is a header preview, not stream reassembly or application identification.

`summarize()` returns the one-line summary the inspector lists (protocol, addresses, ports,
`info`) plus, since 2026-10-05, what its detail view shows when a row is opened: `layers`, one
entry per header with its decoded fields in order, and `header_hex`, the bytes of those headers
only (Ethernet through the transport header, options included), so a packet can be read as the
wire carried it while its payload is never kept.
"""
from __future__ import annotations

import ipaddress
from typing import Any

ETHERTYPES = {0x0800: "IPv4", 0x0806: "ARP", 0x86dd: "IPv6", 0x8100: "VLAN (802.1Q)", 0x88a8: "VLAN (802.1ad)"}
IP_PROTOCOLS = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP", 41: "IPv6", 47: "GRE", 50: "ESP", 51: "AH", 58: "ICMPv6", 89: "OSPF", 132: "SCTP"}
IPV6_EXTENSIONS = {0: "Hop-by-hop options", 43: "Routing", 44: "Fragment", 51: "Authentication", 60: "Destination options"}
TCP_FLAGS = [(0x02, "SYN"), (0x10, "ACK"), (0x01, "FIN"), (0x04, "RST"), (0x08, "PSH"), (0x20, "URG"), (0x40, "ECE"), (0x80, "CWR")]
ICMP_TYPES = {0: "Echo reply", 8: "Echo request", 3: "Destination unreachable", 11: "Time exceeded", 5: "Redirect"}
ICMPV6_TYPES = {128: "Echo request", 129: "Echo reply", 1: "Destination unreachable", 2: "Packet too big", 3: "Time exceeded",
                4: "Parameter problem", 135: "Neighbor solicitation", 136: "Neighbor advertisement",
                133: "Router solicitation", 134: "Router advertisement"}


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "big")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "big")


def _proto(number: int) -> str:
    name = IP_PROTOCOLS.get(number)
    return f"{name} ({number})" if name else str(number)


def summarize(frame: bytes) -> dict[str, Any] | None:
    if len(frame) < 14:
        return None
    layers: list[dict[str, Any]] = []
    result: dict[str, Any] = {"source_mac": frame[6:12].hex(":"), "destination_mac": frame[:6].hex(":"),
                              "source": frame[6:12].hex(":"), "destination": frame[:6].hex(":"),
                              "protocol": "Ethernet", "info": "", "bytes": len(frame), "multicast": bool(frame[0] & 1),
                              "layers": layers, "header_hex": ""}
    # Header parsing and copies stay small even for jumbo frames.
    data = frame[:256]
    kind, offset = _u16(data, 12), 14
    ethernet_fields = [["Destination", result["destination_mac"]], ["Source", result["source_mac"]]]
    for _ in range(2):
        if kind not in (0x8100, 0x88a8):
            break
        if len(data) < offset + 4:
            result["info"] = "Truncated VLAN header"
            return _finish(result, data, offset, ethernet_fields, kind)
        tag = _u16(data, offset)
        result["info"] += f"VLAN {tag & 0xfff} · "
        ethernet_fields.append(["VLAN", f"id {tag & 0xfff} · priority {tag >> 13}"])
        kind, offset = _u16(data, offset + 2), offset + 4
    ethernet_fields.append(["Type", f"{ETHERTYPES.get(kind, 'unknown')} (0x{kind:04x})"])
    layers.append({"name": "Ethernet", "fields": ethernet_fields})
    packet = data[offset:]
    header_end = offset
    if kind == 0x0806:
        result["protocol"] = "ARP"
        if len(packet) >= 28 and packet[:6] == b"\x00\x01\x08\x00\x06\x04":
            result.update(source=str(ipaddress.IPv4Address(packet[14:18])),
                          destination=str(ipaddress.IPv4Address(packet[24:28])))
            operation = _u16(packet, 6)
            result["info"] += {1: "Who has this IP?", 2: "This IP is at " + result["source_mac"]}.get(operation, f"Operation {operation}")
            layers.append({"name": "ARP", "fields": [
                ["Hardware / protocol", "Ethernet / IPv4"], ["Operation", {1: "request (1)", 2: "reply (2)"}.get(operation, str(operation))],
                ["Sender MAC", packet[8:14].hex(":")], ["Sender IP", result["source"]],
                ["Target MAC", packet[18:24].hex(":")], ["Target IP", result["destination"]]]})
            header_end = offset + 28
        else:
            result["info"] += "Unsupported or truncated ARP header"
        return _finish(result, data, header_end)
    if kind == 0x0800:
        result["protocol"] = "IPv4"
        header_len = (packet[0] & 15) * 4 if packet else 0
        if len(packet) < 20 or packet[0] >> 4 != 4 or header_len < 20 or len(packet) < header_len or _u16(packet, 2) < header_len:
            result["info"] += "Invalid or truncated IPv4 header"
            return _finish(result, data, header_end)
        result.update(source=str(ipaddress.IPv4Address(packet[12:16])),
                      destination=str(ipaddress.IPv4Address(packet[16:20])))
        protocol = packet[9]
        flags, fragment_offset = packet[6] >> 5, _u16(packet, 6) & 0x1fff
        flag_names = [name for bit, name in ((2, "DF"), (1, "MF")) if flags & bit]
        layers.append({"name": "IPv4", "fields": [
            ["Version / header length", f"4 / {header_len} bytes" + (f" ({header_len - 20} bytes of options)" if header_len > 20 else "")],
            ["DSCP / ECN", f"{packet[1] >> 2} / {packet[1] & 3}"], ["Total length", str(_u16(packet, 2))],
            ["Identification", f"0x{_u16(packet, 4):04x} ({_u16(packet, 4)})"],
            ["Flags / fragment offset", f"{' '.join(flag_names) or 'none'} / {fragment_offset * 8} bytes"],
            ["TTL", str(packet[8])], ["Protocol", _proto(protocol)], ["Header checksum", f"0x{_u16(packet, 10):04x}"],
            ["Source", result["source"]], ["Destination", result["destination"]]]})
        header_end = offset + header_len
        if fragment_offset:
            result["info"] += "IP fragment · transport header unavailable"
            return _finish(result, data, header_end)
        payload = packet[header_len:_u16(packet, 2)]
    elif kind == 0x86dd:
        result["protocol"] = "IPv6"
        if len(packet) < 40 or packet[0] >> 4 != 6:
            result["info"] += "Invalid or truncated IPv6 header"
            return _finish(result, data, header_end)
        result.update(source=str(ipaddress.IPv6Address(packet[8:24])),
                      destination=str(ipaddress.IPv6Address(packet[24:40])))
        protocol, pos = packet[6], 40
        first = _u32(packet, 0)
        extensions: list[str] = []
        ipv6_fields = [["Version", "6"], ["Traffic class", str((first >> 20) & 0xff)], ["Flow label", f"0x{first & 0xfffff:05x}"],
                       ["Payload length", str(_u16(packet, 4))], ["Next header", _proto(protocol)], ["Hop limit", str(packet[7])],
                       ["Source", result["source"]], ["Destination", result["destination"]]]
        layers.append({"name": "IPv6", "fields": ipv6_fields})
        packet = packet[:40 + _u16(packet, 4)]
        for _ in range(8):
            if protocol not in IPV6_EXTENSIONS:
                break
            if len(packet) < pos + 8:
                result["info"] += "Truncated IPv6 extension"
                return _finish(result, data, offset + pos)
            if protocol == 44 and _u16(packet, pos + 2) & 0xfff8:
                result["info"] += "IP fragment · transport header unavailable"
                extensions.append(IPV6_EXTENSIONS[protocol])
                ipv6_fields.append(["Extension headers", ", ".join(extensions)])
                return _finish(result, data, offset + pos + 8)
            length = 8 if protocol == 44 else (packet[pos + 1] + (2 if protocol == 51 else 1)) * (4 if protocol == 51 else 8)
            if pos + length > len(packet):
                result["info"] += "Truncated IPv6 extension"
                return _finish(result, data, offset + pos)
            extensions.append(IPV6_EXTENSIONS[protocol])
            protocol, pos = packet[pos], pos + length
        if extensions:
            ipv6_fields.append(["Extension headers", ", ".join(extensions)])
        header_end = offset + pos
        payload = packet[pos:]
    else:
        result["info"] += f"EtherType 0x{kind:04x}"
        return _finish(result, data, header_end)
    if protocol in (6, 17):
        result["protocol"] = "TCP" if protocol == 6 else "UDP"
        required = 20 if protocol == 6 else 8
        if len(payload) < required or (protocol == 6 and not 20 <= (payload[12] >> 4) * 4 <= len(payload)) or (protocol == 17 and _u16(payload, 4) < 8):
            result["info"] += "Invalid or truncated transport header"
            return _finish(result, data, header_end)
        result.update(source_port=_u16(payload, 0), destination_port=_u16(payload, 2))
        if protocol == 6:
            data_offset = (payload[12] >> 4) * 4
            names = [name for bit, name in TCP_FLAGS if payload[13] & bit]
            result["info"] += " ".join(names) or "TCP segment"
            layers.append({"name": "TCP", "fields": [
                ["Source port", str(result["source_port"])], ["Destination port", str(result["destination_port"])],
                ["Sequence number", str(_u32(payload, 4))], ["Acknowledgment number", str(_u32(payload, 8))],
                ["Header length", f"{data_offset} bytes" + (f" ({data_offset - 20} bytes of options)" if data_offset > 20 else "")],
                ["Flags", f"{' '.join(names) or 'none'} (0x{payload[13]:02x})"], ["Window", str(_u16(payload, 14))],
                ["Checksum", f"0x{_u16(payload, 16):04x}"], ["Urgent pointer", str(_u16(payload, 18))],
                ["Payload", f"{max(0, len(payload) - data_offset)} bytes (not captured)"]]})
            header_end += data_offset
        else:
            result["info"] += "UDP datagram"
            layers.append({"name": "UDP", "fields": [
                ["Source port", str(result["source_port"])], ["Destination port", str(result["destination_port"])],
                ["Length", f"{_u16(payload, 4)} bytes (header included)"], ["Checksum", f"0x{_u16(payload, 6):04x}"],
                ["Payload", f"{max(0, _u16(payload, 4) - 8)} bytes (not captured)"]]})
            header_end += 8
    elif protocol in (1, 58):
        result["protocol"] = "ICMP" if protocol == 1 else "ICMPv6"
        if len(payload) < 4:
            result["info"] += "Truncated ICMP header"
            return _finish(result, data, header_end)
        types = ICMP_TYPES if protocol == 1 else ICMPV6_TYPES
        name = types.get(payload[0], f"Type {payload[0]} code {payload[1]}")
        result["info"] += name
        fields = [["Type", f"{name} ({payload[0]})"], ["Code", str(payload[1])], ["Checksum", f"0x{_u16(payload, 2):04x}"]]
        if payload[0] in ((0, 8) if protocol == 1 else (128, 129)) and len(payload) >= 8:
            result["info"] += f" · id={_u16(payload, 4)} seq={_u16(payload, 6)}"
            fields += [["Identifier", str(_u16(payload, 4))], ["Sequence number", str(_u16(payload, 6))],
                       ["Payload", f"{len(payload) - 8} bytes (not captured)"]]
            header_end += 8
        else:
            header_end += 4
        layers.append({"name": result["protocol"], "fields": fields})
    else:
        result["info"] += f"IP protocol {protocol}"
    return _finish(result, data, header_end)


def _finish(result: dict[str, Any], data: bytes, header_end: int, ethernet_fields: list[list[str]] | None = None,
            kind: int | None = None) -> dict[str, Any]:
    """The header bytes as hex (never past what was parsed, never the payload) and the layers."""
    if ethernet_fields is not None and not result["layers"]:  # a VLAN header cut short: the Ethernet layer as far as it goes
        result["layers"].append({"name": "Ethernet", "fields": ethernet_fields + [["Type", f"0x{kind:04x}" if kind is not None else "?"]]})
    result["header_hex"] = data[:max(14, min(header_end, len(data)))].hex()
    return result
