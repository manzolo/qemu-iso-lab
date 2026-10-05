"""Read-only lab telemetry: count Ethernet frames on QEMU's loopback multicast.

No packet payload is retained or transmitted. Listeners expire when the map is closed.
Segment counters describe observed frames, not guest delivery or connectivity. NAT
counters, when available, come from QGA and do not generate guest network traffic.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Any

from vmctl import config, guest_agent, qemu
from vmctl.errors import VMError


IDLE_SECONDS = 15


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
        except OSError as exc:
            self.error = str(exc)
        finally:
            self.stop.set()
            self.sock.close()

    def sample(self, macs: list[str]) -> dict[str, Any]:
        with self.lock:
            self.last_used = time.monotonic()
            for mac in macs:
                self.counters.setdefault(mac, {"rx": 0, "tx": 0})
            return {"time": self.last_used, "epoch": self.epoch,
                    "counters": {mac: dict(self.counters[mac]) for mac in macs}}

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=1)


class TrafficMonitor:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sample_lock = threading.Lock()
        self.listeners: dict[str, SegmentListener] = {}

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
            for nic in qemu.network_specs(vm):
                entry = {"vm": member["name"], "nic": nic["id"], "mac": nic["mac"],
                         "available": False, "reason": "VM stopped" if not member["running"] else "Traffic unavailable"}
                entries.append(entry)
                if not member["running"]:
                    continue
                stats = agent_stats.get(nic["mac"])
                if isinstance(stats, dict) and all(type(stats.get(key)) is int and stats[key] >= 0 for key in ("rx-bytes", "tx-bytes")):
                    entry.update(available=True, source="guest agent", rx=stats["rx-bytes"], tx=stats["tx-bytes"],
                                 time=time.monotonic(), epoch="qga")
                elif nic["type"] == "segment":
                    endpoint = qemu.segment_endpoint(str(nic["name"]), nic["mcast"])
                    segments.setdefault(endpoint, []).append(entry)
                else:
                    entry["reason"] = "NAT traffic needs guest-agent counters"
        for endpoint, members in segments.items():
            try:
                sample = self.segment(endpoint, [m["mac"] for m in members])
                for entry in members:
                    entry.update(available=True, source="segment frames", time=sample["time"], epoch=sample["epoch"],
                                 **sample["counters"][entry["mac"]])
            except (OSError, ValueError, VMError) as exc:
                for entry in members:
                    entry["reason"] = f"Segment capture unavailable: {exc}"
        return entries

    def close(self) -> None:
        with self.lock:
            for listener in self.listeners.values():
                listener.close()
            self.listeners.clear()


monitor = TrafficMonitor()
