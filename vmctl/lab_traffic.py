"""Read-only lab telemetry: count Ethernet frames on QEMU's loopback multicast, and on a NAT
link read what QEMU's own ``filter-dump`` writes.

No packet payload is retained or transmitted. Listeners expire when the map is closed.
Segment counters describe observed frames, not guest delivery or connectivity. NAT links
(slirp, nothing on the host to listen to) get a ``filter-dump`` object hot-added over QMP
(2026-10-05, "Traffic unavailable" on pfSense, which has no guest agent): QEMU writes the
first ``PCAP_SNAPLEN`` bytes of every frame to a pcap under the VM's runtime directory,
``NatCapture`` tails it, and the object and the file go away ``IDLE_SECONDS`` after the last
poll, at ``TrafficMonitor.close`` or at exit. Guest-agent counters are the fallback.
"""
from __future__ import annotations

import atexit
import socket
import struct
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterator

from vmctl import config, guest_agent, packet_summary, qemu
from vmctl.errors import VMError


IDLE_SECONDS = 15
PACKET_HISTORY = 128
PACKET_SECONDS = 30
PACKETS_PER_SECOND = 100
PACKETS_PER_NIC = 24
PCAP_SNAPLEN = 128   # Ethernet + IPv6 + a TCP header with options; the payload stays out
# OFF until understood on a disposable VM: on 2026-10-05 the first live use on pfsense-lab left
# QEMU's QMP monitor answering nothing (every command timed out, the guest kept running) with a
# filter-dump writing into an unlinked file (108 MB in ten minutes). The code and its tests stay.
NAT_CAPTURE = False
NAT_REASON = "NAT traffic needs the VM's QMP socket (a headless start) or guest-agent counters"


class PacketHistory:
    """A short, bounded sample of headers; byte counters remain unsampled."""
    def __init__(self) -> None:
        self.records: deque[dict[str, Any]] = deque(maxlen=PACKET_HISTORY)
        self.window = 0.0
        self.recorded = 0
        self.sequence = 0

    def add(self, frame: bytes, now: float, length: int | None = None) -> None:
        """``length`` is the frame's size on the wire when ``frame`` is a truncated capture."""
        if now - self.window >= 1:
            self.window, self.recorded = now, 0
        if self.recorded >= PACKETS_PER_SECOND:
            return
        self.recorded += 1
        summary = packet_summary.summarize(frame)
        if summary is not None:
            if length is not None:
                summary["bytes"] = max(length, summary["bytes"])
            self.sequence += 1
            self.records.append({**summary, "id": self.sequence, "time": time.time(), "observed": now})

    def for_mac(self, mac: str, now: float) -> list[dict[str, Any]]:
        while self.records and self.records[0]["observed"] < now - PACKET_SECONDS:
            self.records.popleft()
        found = []
        for record in reversed(self.records):
            direction = "TX" if record["source_mac"] == mac else "RX" if record["destination_mac"] == mac or record["multicast"] else ""
            if direction:
                found.append({key: value for key, value in record.items() if key != "observed"} | {"direction": direction})
            if len(found) >= PACKETS_PER_NIC:
                break
        return found


def count_frame(frame: bytes, counters: dict[str, dict[str, int]]) -> None:
    """Attribute source TX and destination RX; multicast reaches every other port."""
    if len(frame) < 14:
        return
    source, destination = frame[6:12].hex(":"), frame[:6].hex(":")
    if source in counters:
        counters[source]["tx"] += len(frame)
    if frame[0] & 1:
        for mac, values in counters.items():
            if mac != source:
                values["rx"] += len(frame)
    elif destination in counters and destination != source:
        counters[destination]["rx"] += len(frame)


class SegmentListener:
    def __init__(self, endpoint: str):
        self.lock = threading.Lock()
        self.counters: dict[str, dict[str, int]] = {}
        self.history = PacketHistory()
        self.last_used = time.monotonic()
        self.epoch = self.last_used
        self.error = ""
        self.stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            address, port = endpoint.rsplit(":", 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((address, int(port)))
            membership = socket.inet_aton(address) + socket.inet_aton(qemu.MCAST_LOCALADDR)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            self.sock.settimeout(.5)
        except (OSError, ValueError):
            self.sock.close()
            raise
        self.thread = threading.Thread(target=self._receive, name="lab-traffic", daemon=True)
        self.thread.start()

    def _receive(self) -> None:
        try:
            while not self.stop.is_set():
                with self.lock:
                    if time.monotonic() - self.last_used > IDLE_SECONDS:
                        break
                try:
                    frame = self.sock.recv(65535)
                except socket.timeout:
                    continue
                with self.lock:
                    count_frame(frame, self.counters)
                    self.history.add(frame, time.monotonic())
        except OSError as exc:
            self.error = str(exc)
        finally:
            self.stop.set()
            self.sock.close()
            with self.lock:
                self.history.records.clear()

    def sample(self, macs: list[str]) -> dict[str, Any]:
        with self.lock:
            self.last_used = time.monotonic()
            for mac in macs:
                self.counters.setdefault(mac, {"rx": 0, "tx": 0})
            return {"time": self.last_used, "epoch": self.epoch,
                    "packets": {mac: self.history.for_mac(mac, self.last_used) for mac in macs},
                    "counters": {mac: dict(self.counters[mac]) for mac in macs}}

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=1)


class PcapReader:
    """Incremental reader of a pcap stream (the file QEMU's filter-dump appends to): yields
    ``(length on the wire, captured bytes)`` per complete record and keeps a partial one."""
    MAGICS = {b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">", b"\x4d\x3c\xb2\xa1": "<", b"\xa1\xb2\x3c\x4d": ">"}

    def __init__(self) -> None:
        self.buffer = b""
        self.order = ""

    def feed(self, chunk: bytes) -> Iterator[tuple[int, bytes]]:
        self.buffer += chunk
        if not self.order:
            if len(self.buffer) < 24:
                return
            try:
                self.order = self.MAGICS[self.buffer[:4]]
            except KeyError:
                raise ValueError("not a pcap file") from None
            self.buffer = self.buffer[24:]
        while len(self.buffer) >= 16:
            _, _, captured, length = struct.unpack(self.order + "IIII", self.buffer[:16])
            if len(self.buffer) < 16 + captured:
                return
            yield length, self.buffer[16:16 + captured]
            self.buffer = self.buffer[16 + captured:]


class NatCapture:
    """QEMU's filter-dump on one NAT netdev of a running headless VM, read as it is written."""
    def __init__(self, vm_name: str, vm: dict[str, Any], nic: str, mac: str,
                 qmp: Callable[..., Any] | None = None, sock: Path | None = None) -> None:
        self.vm, self.nic, self.mac = vm_name, nic, mac
        self.qmp = qmp or qemu.qmp_execute
        try:
            self.sock = sock or qemu.qmp_socket_path(vm)
        except (KeyError, TypeError):
            raise VMError("no QMP socket") from None
        if not self.sock.exists():
            raise VMError("no QMP socket: the VM did not start headless")
        self.path = self.sock.parent / f"capture-{nic}.pcap"
        self.object_id = f"vmctl-dump-{nic}"
        self.lock = threading.Lock()
        self.counters = {"rx": 0, "tx": 0}
        self.history = PacketHistory()
        self.last_used = time.monotonic()
        self.epoch = self.last_used
        self.error = ""
        self.stop = threading.Event()
        self._remove_file()
        self._attach()
        self.thread = threading.Thread(target=self._read, name=f"nat-capture-{vm_name}-{nic}", daemon=True)
        self.thread.start()

    def _qmp(self, command: str, **arguments: Any) -> Any:
        with qemu.QMP_LOCK:
            return self.qmp(self.sock, command, arguments=arguments, timeout=3)

    def _attach(self) -> None:
        arguments = {"qom-type": "filter-dump", "id": self.object_id, "netdev": self.nic,
                     "file": str(self.path), "maxlen": PCAP_SNAPLEN}
        for attempt in range(3):  # another vmctl (a second dashboard) may hold the one QMP client slot
            try:
                self._qmp("object-add", **arguments)
                return
            except VMError as exc:
                if "duplicate" in str(exc):
                    self._qmp("object-del", id=self.object_id)  # left behind by a server that died
                    self._qmp("object-add", **arguments)
                    return
                if attempt == 2 or not any(word in str(exc) for word in ("reset", "refused", "Broken pipe", "closed")):
                    raise
                time.sleep(.3)

    def _detach(self) -> None:
        try:
            self._qmp("object-del", id=self.object_id)
        except VMError:
            pass  # QEMU is gone, or the object already is
        self._remove_file()

    def _remove_file(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.error = str(exc)

    def _read(self) -> None:
        reader, position = PcapReader(), 0
        try:
            while not self.stop.is_set():
                with self.lock:
                    if time.monotonic() - self.last_used > IDLE_SECONDS:
                        break
                try:
                    with open(self.path, "rb") as handle:
                        handle.seek(position)
                        chunk = handle.read()
                except FileNotFoundError:
                    chunk = b""
                if not chunk:
                    if not self.sock.exists():
                        break  # QEMU exited: nothing more will be written
                    time.sleep(.25)
                    continue
                position += len(chunk)
                now = time.monotonic()
                with self.lock:
                    for length, data in reader.feed(chunk):
                        if len(data) >= 14:
                            self.counters["tx" if data[6:12].hex(":") == self.mac else "rx"] += length
                        self.history.add(data, now, length)
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        finally:
            self.stop.set()
            self._detach()
            with self.lock:
                self.history.records.clear()

    def sample(self) -> dict[str, Any]:
        with self.lock:
            self.last_used = time.monotonic()
            return {"time": self.last_used, "epoch": self.epoch, "packets": self.history.for_mac(self.mac, self.last_used),
                    "counters": dict(self.counters)}

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=3)


class TrafficMonitor:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sample_lock = threading.Lock()
        self.listeners: dict[str, SegmentListener] = {}
        self.captures: dict[str, NatCapture] = {}

    def nat(self, vm_name: str, vm: dict[str, Any], nic: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.captures = {key: value for key, value in self.captures.items() if not value.stop.is_set()}
            key = f"{vm_name}/{nic['id']}"
            if key not in self.captures:
                if len(self.captures) >= 64:
                    raise VMError("Too many monitored NAT links")
                self.captures[key] = NatCapture(vm_name, vm, str(nic["id"]), str(nic["mac"]))
            return self.captures[key].sample()

    def segment(self, endpoint: str, macs: list[str]) -> dict[str, Any]:
        with self.lock:
            # Dead/expired listeners hold neither sockets nor packet buffers.
            self.listeners = {key: value for key, value in self.listeners.items() if not value.stop.is_set()}
            if endpoint not in self.listeners:
                if len(self.listeners) >= 64:
                    raise VMError("Too many monitored segments")
                self.listeners[endpoint] = SegmentListener(endpoint)
            return self.listeners[endpoint].sample(macs)

    def sample(self, cfg: dict[str, Any], lab: dict[str, Any]) -> list[dict[str, Any]]:
        # Several open maps must not query the same guest agent concurrently.
        with self.sample_lock:
            return self._sample(cfg, lab)

    def _sample(self, cfg: dict[str, Any], lab: dict[str, Any]) -> list[dict[str, Any]]:
        entries = []
        segments: dict[str, list[dict[str, Any]]] = {}
        for member in lab["members"]:
            vm = config.get_vm(cfg, member["name"])
            agent_stats = {}
            if member["running"] and guest_agent.enabled(vm):
                try:
                    interfaces = guest_agent.command(vm, "guest-network-get-interfaces", timeout=.5)
                    for interface in interfaces if isinstance(interfaces, list) else []:
                        if isinstance(interface, dict):
                            agent_stats[str(interface.get("hardware-address", "")).lower()] = interface.get("statistics")
                except VMError:
                    pass
            for nic in member.get("nics") or qemu.network_specs(vm):  # the model's NICs include a link session's
                entry = {"vm": member["name"], "nic": nic["id"], "mac": nic["mac"],
                         "packets_available": False, "packets": [],
                         "packet_reason": "VM stopped" if not member["running"] else "Waiting for capture information…",
                         "available": False, "reason": "VM stopped" if not member["running"] else "Traffic unavailable"}
                entries.append(entry)
                if not member["running"]:
                    continue
                if nic["type"] == "user" and NAT_CAPTURE:
                    # QEMU's own capture first: it needs no agent and brings the headers too.
                    try:
                        sample = self.nat(member["name"], vm, nic)
                        entry.update(available=True, source="QEMU capture", time=sample["time"], epoch=sample["epoch"],
                                     packets_available=True, packet_reason="", packets=sample["packets"], **sample["counters"])
                    except (OSError, ValueError, VMError) as exc:
                        entry["packet_reason"] = f"NAT capture unavailable: {exc}"
                stats = agent_stats.get(nic["mac"])
                if not entry["available"] and isinstance(stats, dict) and all(type(stats.get(key)) is int and stats[key] >= 0 for key in ("rx-bytes", "tx-bytes")):
                    entry.update(available=True, source="guest agent", rx=stats["rx-bytes"], tx=stats["tx-bytes"],
                                 time=time.monotonic(), epoch="qga")
                if nic["type"] == "segment":
                    endpoint = qemu.segment_endpoint(str(nic.get("segment") or nic.get("name")), nic.get("mcast"))
                    segments.setdefault(endpoint, []).append(entry)
                elif not entry["available"]:
                    entry["reason"] = NAT_REASON
        for endpoint, members in segments.items():
            try:
                sample = self.segment(endpoint, [m["mac"] for m in members])
                for entry in members:
                    if not entry["available"]:
                        entry.update(available=True, source="segment frames", time=sample["time"], epoch=sample["epoch"],
                                     **sample["counters"][entry["mac"]])
                    entry.update(packets_available=True, packet_reason="", packets=sample.get("packets", {}).get(entry["mac"], []))
            except (OSError, ValueError, VMError) as exc:
                for entry in members:
                    entry["packet_reason"] = f"Segment capture unavailable: {exc}"
                    if not entry["available"]:
                        entry["reason"] = entry["packet_reason"]
        return entries

    def close(self) -> None:
        with self.lock:
            for listener in self.listeners.values():
                listener.close()
            self.listeners.clear()
            for capture in self.captures.values():
                capture.close()  # the thread's exit removes the filter-dump object and its file
            self.captures.clear()


monitor = TrafficMonitor()
atexit.register(monitor.close)
