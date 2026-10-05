"""Bounded Ethernet/IP header summaries for the lab map; never retain payloads.

IPv4/ICMP layouts: RFC 791/792; IPv6 extensions: RFC 8200; TCP: RFC 9293.
This is a header preview, not stream reassembly or application identification.
"""
from __future__ import annotations

import ipaddress
from typing import Any


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "big")


def summarize(frame: bytes) -> dict[str, Any] | None:
    if len(frame) < 14:
        return None
    result: dict[str, Any] = {"source_mac": frame[6:12].hex(":"), "destination_mac": frame[:6].hex(":"),
              "source": frame[6:12].hex(":"), "destination": frame[:6].hex(":"),
              "protocol": "Ethernet", "info": "", "bytes": len(frame), "multicast": bool(frame[0] & 1)}
    # Header parsing and copies stay small even for jumbo frames.
    data = frame[:256]
    kind, offset = _u16(data, 12), 14
    for _ in range(2):
        if kind not in (0x8100, 0x88a8):
            break
        if len(data) < offset + 4:
            result["info"] = "Truncated VLAN header"
            return result
        result["info"] += f"VLAN {_u16(data, offset) & 0xfff} · "
        kind, offset = _u16(data, offset + 2), offset + 4
    packet = data[offset:]
    if kind == 0x0806:
        result["protocol"] = "ARP"
        if len(packet) >= 28 and packet[:6] == b"\x00\x01\x08\x00\x06\x04":
            result.update(source=str(ipaddress.IPv4Address(packet[14:18])),
                          destination=str(ipaddress.IPv4Address(packet[24:28])))
            operation = _u16(packet, 6)
            result["info"] += {1: "Who has this IP?", 2: "This IP is at " + result["source_mac"]}.get(operation, f"Operation {operation}")
        else:
            result["info"] += "Unsupported or truncated ARP header"
        return result
    if kind == 0x0800:
        result["protocol"] = "IPv4"
        header_len = (packet[0] & 15) * 4 if packet else 0
        if len(packet) < 20 or packet[0] >> 4 != 4 or header_len < 20 or len(packet) < header_len or _u16(packet, 2) < header_len:
            result["info"] += "Invalid or truncated IPv4 header"
            return result
        result.update(source=str(ipaddress.IPv4Address(packet[12:16])),
                      destination=str(ipaddress.IPv4Address(packet[16:20])))
        protocol = packet[9]
        if _u16(packet, 6) & 0x1fff:
            result["info"] += "IP fragment · transport header unavailable"
            return result
        payload = packet[header_len:_u16(packet, 2)]
    elif kind == 0x86dd:
        result["protocol"] = "IPv6"
        if len(packet) < 40 or packet[0] >> 4 != 6:
            result["info"] += "Invalid or truncated IPv6 header"
            return result
        result.update(source=str(ipaddress.IPv6Address(packet[8:24])),
                      destination=str(ipaddress.IPv6Address(packet[24:40])))
        protocol, pos = packet[6], 40
        packet = packet[:40 + _u16(packet, 4)]
        for _ in range(8):
            if protocol not in (0, 43, 44, 51, 60):
                break
            if len(packet) < pos + 8:
                result["info"] += "Truncated IPv6 extension"
                return result
            if protocol == 44 and _u16(packet, pos + 2) & 0xfff8:
                result["info"] += "IP fragment · transport header unavailable"
                return result
            length = 8 if protocol == 44 else (packet[pos + 1] + (2 if protocol == 51 else 1)) * (4 if protocol == 51 else 8)
            if pos + length > len(packet):
                result["info"] += "Truncated IPv6 extension"
                return result
            protocol, pos = packet[pos], pos + length
        payload = packet[pos:]
    else:
        result["info"] += f"EtherType 0x{kind:04x}"
        return result
    if protocol in (6, 17):
        result["protocol"] = "TCP" if protocol == 6 else "UDP"
        required = 20 if protocol == 6 else 8
        if len(payload) < required or (protocol == 6 and not 20 <= (payload[12] >> 4) * 4 <= len(payload)) or (protocol == 17 and _u16(payload, 4) < 8):
            result["info"] += "Invalid or truncated transport header"
            return result
        result.update(source_port=_u16(payload, 0), destination_port=_u16(payload, 2))
        if protocol == 6:
            flags = [(0x02, "SYN"), (0x10, "ACK"), (0x01, "FIN"), (0x04, "RST"), (0x08, "PSH"), (0x20, "URG"), (0x40, "ECE"), (0x80, "CWR")]
            result["info"] += " ".join(name for bit, name in flags if payload[13] & bit) or "TCP segment"
        else:
            result["info"] += "UDP datagram"
    elif protocol in (1, 58):
        result["protocol"] = "ICMP" if protocol == 1 else "ICMPv6"
        if len(payload) < 4:
            result["info"] += "Truncated ICMP header"
            return result
        types = {0: "Echo reply", 8: "Echo request", 3: "Destination unreachable", 11: "Time exceeded"} if protocol == 1 else {
            128: "Echo request", 129: "Echo reply", 1: "Destination unreachable", 3: "Time exceeded",
            135: "Neighbor solicitation", 136: "Neighbor advertisement", 133: "Router solicitation", 134: "Router advertisement"}
        result["info"] += types.get(payload[0], f"Type {payload[0]} code {payload[1]}")
        if payload[0] in ((0, 8) if protocol == 1 else (128, 129)) and len(payload) >= 8:
            result["info"] += f" · id={_u16(payload, 4)} seq={_u16(payload, 6)}"
    else:
        result["info"] += f"IP protocol {protocol}"
    return result
